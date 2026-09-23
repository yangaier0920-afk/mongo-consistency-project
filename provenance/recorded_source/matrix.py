"""Sequential experiment matrix; never run faults concurrently on the same cluster."""
import argparse
import json
import random
import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+', choices=['ryw', 'wfr', 'mr', 'mw'], default=['ryw', 'wfr'])
    parser.add_argument('--scenarios', nargs='+', choices=['normal', 'failure', 'partition'], default=['normal'])
    parser.add_argument('--profiles', nargs='+', choices=['weak', 'majority', 'causal'], default=['weak', 'causal'])
    parser.add_argument('--iterations', type=int, default=100)
    parser.add_argument('--fault-at', type=int, default=20)
    parser.add_argument('--recover-at', type=int, default=60)
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--seed', type=int, default=5208)
    parser.add_argument('--output', type=Path, default=Path('results/final'))
    parser.add_argument('--timeout-ms', type=int, default=5000)
    parser.add_argument('--observe-timeout-ms', type=int, default=1000)
    args = parser.parse_args()
    if args.repeats < 1 or args.iterations < 1 or args.timeout_ms < 1 or args.observe_timeout_ms < 0:
        parser.error('repeats, iterations and timeout-ms must be positive; observe-timeout-ms must be nonnegative')
    if any(s != 'normal' for s in args.scenarios) and not (1 <= args.fault_at < args.recover_at <= args.iterations):
        parser.error('Fault runs require 1 <= fault-at < recover-at <= iterations')
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve() / ('matrix_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ_') + uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    generator = random.Random(args.seed)
    schedule = []
    for repeat in range(1, args.repeats + 1):
        block = [(model, scenario, profile) for model in args.models
                 for scenario in args.scenarios for profile in args.profiles]
        generator.shuffle(block)
        schedule.extend({'repeat': repeat, 'model': m, 'scenario': s, 'profile': p} for m, s, p in block)
    plan = {'schema_version': 3, 'args': vars(args), 'schedule': schedule,
            'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in Path(__file__).parent.glob('*.py')}}
    (output / 'plan.json').write_text(json.dumps(plan, indent=2, default=str), encoding='utf-8')
    print(f'Matrix: {output}; planned runs: {len(schedule)}', flush=True)
    results = []
    for position, item in enumerate(schedule, 1):
        repeat, model, scenario, profile = (item[k] for k in ('repeat', 'model', 'scenario', 'profile'))
        print(f'Run {position}/{len(schedule)}: {item}', flush=True)
        existing = set(output.iterdir())
        command = [sys.executable, '-m', 'experiments_v2.run', '--model', model,
                   '--scenario', scenario, '--profile', profile, '--iterations', str(args.iterations),
                   '--fault-at', str(args.fault_at), '--recover-at', str(args.recover_at), '--output', str(output),
                   '--timeout-ms', str(args.timeout_ms), '--observe-timeout-ms', str(args.observe_timeout_ms)]
        result = subprocess.run(command, cwd=root)
        created = [p for p in set(output.iterdir()) - existing if p.is_dir()]
        passed = result.returncode == 0 and len(created) == 1
        summary = None
        if passed:
            summary = json.loads((created[0] / 'summary.json').read_text(encoding='utf-8'))
            overall = summary['overall']
            passed = (summary['run_valid'] and overall['eligible'] > 0
                      and (profile != 'causal' or (overall['violations'] == 0 and overall['mr_read_regressions'] == 0))
                      and (scenario != 'normal' or overall['errors'] == 0))
        results.append({'repeat': repeat, 'model': model, 'scenario': scenario,
                        'profile': profile, 'validation_passed': passed,
                        'folder': created[0].name if len(created) == 1 else None, 'summary': summary})
        (output / 'matrix_summary.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        if not passed:
            print(f'STOP: inspect {output}. Do not continue faults before resolving this run.', file=sys.stderr)
            return 2
    print(f'Matrix validation passed. Results: {output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
