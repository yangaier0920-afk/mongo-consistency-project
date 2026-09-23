"""Pure verdict/summary functions; no database or Docker access."""
import math
import json


def dependency_verdict(documents, prerequisite_id, dependent_id):
    """Input MUST come from one independent snapshot, with immutable documents."""
    visible = {doc['_id'] for doc in documents}
    if dependent_id not in visible:
        return 'not_observed'
    return 'pass' if prerequisite_id in visible else 'violation'


def monotonic_write_verdict(documents, first, second):
    """Only visible children with an acknowledged predecessor are eligible."""
    visible = {doc['_id'] for doc in documents}
    children = [d for d in documents if d['_id'] in (first, second) and d.get('depends_on')]
    if not children:
        return 'not_observed'
    return 'violation' if any(d['depends_on'] not in visible for d in children) else 'pass'


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)]


def summarize(rows):
    eligible = [r for r in rows if r['verdict'] in ('pass', 'violation')]
    violations = sum(r['verdict'] == 'violation' for r in eligible)
    latencies = [r['latency_ms'] for r in rows]
    reads = [read for row in rows for read in json.loads(row.get('details_json', '{}')).get('reads', [])]
    completed = [r.get('workload_ms', r['latency_ms']) for r in eligible]
    return {
        'attempts': len(rows),
        'eligible': len(eligible),
        'coverage': len(eligible) / len(rows) if rows else None,
        'violations': violations,
        'violation_rate': violations / len(eligible) if eligible else None,
        'errors': sum(r['verdict'] == 'error' for r in rows),
        'not_observed': sum(r['verdict'] == 'not_observed' for r in rows),
        'error_rate': sum(r['verdict'] == 'error' for r in rows) / len(rows) if rows else None,
        'latency_p50_ms': percentile(latencies, .5),
        'latency_p95_ms': percentile(latencies, .95),
        'eligible_workload_p50_ms': percentile(completed, .5),
        'eligible_workload_p95_ms': percentile(completed, .95),
        'successful_mr_reads': len(reads),
        'mr_read_regressions': sum(r['violation'] for r in reads),
        'mr_read_regression_rate': sum(r['violation'] for r in reads) / len(reads) if reads else None,
    }
