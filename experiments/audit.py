"""Independently audit a completed matrix and export reproducible summary tables.

Usage: python -m experiments.audit results/final/matrix_...
Only the standard library is needed; no live database is touched.
"""
import argparse
import csv
import gzip
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export(path, rows):
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def independently_count(rows):
    counts = Counter(r['verdict'] for r in rows)
    eligible = counts['pass'] + counts['violation']
    reads = [read for row in rows for read in json.loads(row['details_json']).get('reads', [])]
    return dict(attempts=len(rows), eligible=eligible, violations=counts['violation'],
                errors=counts['error'], not_observed=counts['not_observed'],
                coverage=eligible/len(rows) if rows else None,
                violation_rate=counts['violation']/eligible if eligible else None,
                successful_mr_reads=len(reads), mr_read_regressions=sum(r['violation'] for r in reads))


def audit_run(folder, item, plan):
    meta, summary = load(folder/'metadata.json'), load(folder/'summary.json')
    require(meta['status'] == 'valid' and summary['run_valid'], f'{folder.name}: invalid run')
    for key in ('model', 'scenario', 'profile'):
        require(meta[key] == item[key], f'{folder.name}: mismatched {key}')
    require(meta['source_sha256'] == plan['source_sha256'], 'Runner source changed during matrix')
    with (folder/'operations.csv').open(encoding='utf-8', newline='') as stream:
        rows = list(csv.DictReader(stream))
    require(len(rows) == meta['iterations'] == plan['args']['iterations'], 'Wrong row count')
    max_seen = -1
    for i, row in enumerate(rows, 1):
        require(int(row['iteration']) == i and row['run_id'] == meta['run_id'], 'Bad row identity')
        require(row['verdict'] in ('pass', 'violation', 'error', 'not_observed'), 'Unknown verdict')
        expected_phase = ('normal' if meta['scenario'] == 'normal' or i < meta['fault_at'] else
                          'transition' if i == meta['fault_at'] else
                          'fault' if i < meta['recover_at'] else 'recovery')
        require(row['phase'] == expected_phase, 'Wrong phase')
        require(bool(row['error']) == (row['verdict'] == 'error'), 'Error incorrectly classified')
        require(all(math.isfinite(float(row[k])) and float(row[k]) >= 0
                    for k in ('latency_ms', 'management_ms', 'workload_ms')), 'Bad duration')
        require(abs(float(row['latency_ms'])-float(row['management_ms'])-float(row['workload_ms'])) < .003,
                'Management/workload duration mismatch')
        details = json.loads(row['details_json'])
        # Re-evaluate actual observations instead of trusting the recorded flag.
        if meta['model'] == 'mr':
            for read in details.get('reads', []):
                require(read['max_seen_before'] == max_seen, 'MR history was reset')
                require(read['violation'] == (read['version'] < max_seen), 'Incorrect MR comparison')
                max_seen = max(max_seen, read['version'])
        if row['verdict'] not in ('pass', 'violation'):
            continue
        if meta['model'] == 'ryw':
            require(details.get('write_acknowledged'), 'RYW needs acknowledged write')
            violation = details['observed_version'] != i
        elif meta['model'] == 'mr':
            require(len(details['reads']) == 2, 'Incomplete MR pair marked eligible')
            violation = any(r['violation'] for r in details['reads'])
        elif meta['model'] == 'wfr':
            require(details.get('client_b_read_v1') and details.get('dependent_acknowledged'), 'WFR chain incomplete')
            require(f'v2:{i}' in details['visible_ids'], 'WFR dependent not observed')
            violation = f'v1:{i}' not in details['visible_ids']
        else:
            require(details.get('first_acknowledged') and details.get('second_acknowledged'), 'MW chain incomplete')
            edges = [d for d in details['visible_dependencies'] if d.get('depends_on')]
            require(edges, 'MW has no observable dependency')
            violation = any(d['depends_on'] not in details['visible_ids'] for d in edges)
        require((row['verdict'] == 'violation') == violation, 'Incorrect verdict')
    records = []
    for phase in ['overall'] + list(dict.fromkeys(r['phase'] for r in rows)):
        subset = rows if phase == 'overall' else [r for r in rows if r['phase'] == phase]
        counts = independently_count(subset)
        saved = summary['overall'] if phase == 'overall' else summary['by_phase'][phase]
        require(all(saved[k] == v for k, v in counts.items()), 'CSV and summary disagree')
        for prefix, data in (
                ('latency', [float(r['latency_ms']) for r in subset]),
                ('eligible_workload', [float(r['workload_ms']) for r in subset if r['verdict'] in ('pass', 'violation')])):
            ordered = sorted(data)
            for label, fraction in (('p50', .5), ('p95', .95)):
                expected = ordered[math.ceil(len(ordered)*fraction)-1] if ordered else None
                require(saved[f'{prefix}_{label}_ms'] == expected, 'Wrong latency percentile')
        records.append(dict(repeat=item['repeat'], model=item['model'], scenario=item['scenario'],
                            profile=item['profile'], phase=phase, **counts,
                            eligible_workload_p50_ms=saved['eligible_workload_p50_ms'],
                            eligible_workload_p95_ms=saved['eligible_workload_p95_ms'], folder=folder.name))
    require(summary['overall']['eligible'] > 0, 'No eligible observations')
    if meta['scenario'] == 'normal':
        require(summary['overall']['errors'] == 0, 'Normal run contains errors')
    if meta['profile'] == 'causal':
        require(summary['overall']['violations'] == 0 and summary['overall']['mr_read_regressions'] == 0,
                'Causal profile has a counterexample; investigate before accepting')
    final = meta['final_topology']
    require(sum(s.get('primary', False) for s in final) == 1 and
            sum(s.get('secondary', False) for s in final) == 2, 'Unhealthy final topology')
    fault_events, nodes, concerns = [], set(), Counter()
    with gzip.open(folder/'events.jsonl.gz', 'rt', encoding='utf-8') as stream:
        for line in stream:
            event = json.loads(line)
            if event['event'] in ('fault_begin', 'fault_confirmed', 'recovery_begin', 'recovery_confirmed'):
                fault_events.append(event)
            if event['event'] == 'command_started' and event['iteration'] > 0:
                if event['command'] in ('find', 'insert', 'update', 'getMore'):
                    nodes.add(':'.join(map(str, event['node'])))
                rc = event.get('read_concern') or {}
                concerns['snapshot'] += rc.get('level') == 'snapshot'
                concerns['afterClusterTime'] += 'afterClusterTime' in rc
    require(nodes, 'No command evidence')
    if meta['model'] in ('mw', 'wfr'):
        require(concerns['snapshot'] > 0, 'No snapshot measurement evidence')
    if meta['profile'] == 'causal' and meta['model'] in ('ryw', 'mr', 'wfr'):
        require(concerns['afterClusterTime'] > 0, 'No causal read evidence')
    if meta['profile'] != 'causal':
        require(concerns['afterClusterTime'] == 0, 'Unexpected causal read in baseline')
    if meta['scenario'] != 'normal':
        require([e['event'] for e in fault_events] == ['fault_begin', 'fault_confirmed', 'recovery_begin', 'recovery_confirmed'],
                'Missing/duplicate/out-of-order fault evidence')
        begin, proof, restore, recovered = fault_events
        require(begin['iteration'] == meta['fault_at'] and restore['iteration'] == meta['recover_at'], 'Bad fault schedule')
        require(all(e['target'] == meta['fault_target'] for e in fault_events), 'Fault target changed')
        require((not proof['running']) if meta['scenario'] == 'failure' else
                (proof['running'] and meta['network'] not in proof['networks']), 'Fault state not confirmed')
        require(recovered['running'], 'Recovery not confirmed')
        if meta['scenario'] == 'partition':
            require(meta['network'] in recovered['networks'], 'Network not restored')
    return records, dict(folder=folder.name, nodes=sorted(nodes), concerns=dict(concerns))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('matrix', type=Path)
    args = parser.parse_args()
    folder = args.matrix.resolve()
    plan, results = load(folder/'plan.json'), load(folder/'matrix_summary.json')
    require(len(results) == len(plan['schedule']), 'Matrix incomplete')
    records, evidence = [], []
    for item, expected in zip(results, plan['schedule']):
        require(all(item[k] == v for k, v in expected.items()), 'Schedule mismatch')
        require(item['validation_passed'], 'Runner acceptance failed')
        rows, proof = audit_run(folder/item['folder'], item, plan)
        records.extend(rows)
        evidence.append(proof)
    groups = defaultdict(list)
    for row in records:
        groups[tuple(row[k] for k in ('model', 'scenario', 'profile', 'phase'))].append(row)
    aggregate = []
    for key, values in sorted(groups.items()):
        record = dict(zip(('model', 'scenario', 'profile', 'phase'), key))
        record['repeats'] = len(values)
        for metric in ('attempts', 'eligible', 'violations', 'errors', 'not_observed', 'successful_mr_reads', 'mr_read_regressions'):
            record[metric + '_total'] = sum(v[metric] for v in values)
        for metric in ('violation_rate', 'coverage', 'eligible_workload_p50_ms', 'eligible_workload_p95_ms'):
            data = [v[metric] for v in values if v[metric] is not None]
            record[metric + '_mean'] = statistics.mean(data) if data else None
            record[metric + '_sd'] = statistics.stdev(data) if len(data) > 1 else None
            record[metric + '_n'] = len(data)
        aggregate.append(record)
    export(folder/'per_run.csv', records)
    export(folder/'aggregate.csv', aggregate)
    source_matches = {name: (Path(__file__).parent/name).exists() and
                      digest(Path(__file__).parent/name) == value for name, value in plan['source_sha256'].items()}
    require(all(source_matches.values()), 'Current source differs from the code used for this matrix')
    result = dict(audit_passed=True, runs=len(results), attempts=sum(r['attempts'] for r in records if r['phase']=='overall'),
                  current_source_matches=source_matches, evidence=evidence)
    (folder/'audit.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    paths = [folder/'plan.json', folder/'matrix_summary.json', folder/'per_run.csv', folder/'aggregate.csv', folder/'audit.json']
    paths.extend(p for item in results for p in (folder/item['folder']).iterdir() if p.is_file())
    manifest = {str(p.relative_to(folder)).replace('\\', '/'): digest(p) for p in sorted(paths)}
    (folder/'SHA256SUMS.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'evidence'}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
