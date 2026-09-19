"""Run from the repository root: python -m experiments.run --help."""
import argparse
import csv
import gzip
import hashlib
import json
import platform
import sys
import threading
import time
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

import pymongo
from pymongo import MongoClient, ReadPreference, WriteConcern, monitoring
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern

from .checks import dependency_verdict, monotonic_write_verdict, summarize
from .faults import FaultController, FaultError

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    'weak': (1, 'local', False),
    'majority': ('majority', 'majority', False),
    'causal': ('majority', 'majority', True),
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=['ryw', 'wfr', 'mr', 'mw'], required=True)
    parser.add_argument('--scenario', choices=['normal', 'failure', 'partition'], default='normal')
    parser.add_argument('--profile', choices=PROFILES, default='weak')
    parser.add_argument('--iterations', type=int, default=1000)
    parser.add_argument('--fault-at', type=int, default=200)
    parser.add_argument('--recover-at', type=int, default=600)
    parser.add_argument('--uri', default='mongodb://localhost:27017,localhost:27018,localhost:27019/?replicaSet=rs0')
    parser.add_argument('--direct-hosts', default='localhost:27017,localhost:27018,localhost:27019')
    parser.add_argument('--containers', default='mongo1,mongo2,mongo3')
    parser.add_argument('--network', help='Optional exact Docker network name; otherwise discovered.')
    parser.add_argument('--database', default='dsa5208_v2')
    parser.add_argument('--timeout-ms', type=int, default=5000)
    parser.add_argument('--ready-timeout', type=float, default=60)
    parser.add_argument('--observe-timeout-ms', type=int, default=500)
    parser.add_argument('--interval-ms', type=float, default=0)
    parser.add_argument('--output', type=Path, default=ROOT / 'results' / 'revised')
    args = parser.parse_args(argv)
    if args.iterations < 1 or args.timeout_ms < 1 or args.ready_timeout <= 0:
        parser.error('iterations, timeout-ms and ready-timeout must be positive')
    if args.observe_timeout_ms < 0 or args.interval_ms < 0:
        parser.error('observe-timeout-ms and interval-ms must be nonnegative')
    if args.scenario != 'normal' and not (1 <= args.fault_at < args.recover_at <= args.iterations):
        parser.error('Fault runs require 1 <= fault-at < recover-at <= iterations.')
    if len([s for s in args.direct_hosts.split(',') if s.strip()]) != 3:
        parser.error('This experiment requires exactly three direct hosts.')
    if args.scenario != 'normal' and len(args.containers.split(',')) != 3:
        parser.error('Fault runs require exactly three replica containers.')
    return args


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


class Trace(monitoring.CommandListener):
    """Flush evidence immediately; command payload/documents are not dumped."""
    def __init__(self, path):
        self.stream = gzip.open(path, 'wt', encoding='utf-8')
        self.lock = threading.Lock()
        self.iteration = 0
        self.stage = 'preflight'

    def emit(self, event, **data):
        with self.lock:
            self.stream.write(json.dumps({
                'time': time.time(), 'monotonic_ns': time.monotonic_ns(), 'event': event, 'iteration': self.iteration,
                'stage': self.stage, **data,
            }, ensure_ascii=False, default=str) + '\n')
            self.stream.flush()

    def started(self, event):
        self.emit('command_started', command=event.command_name,
                  request_id=event.request_id, node=event.connection_id,
                  read_concern=event.command.get('readConcern'),
                  write_concern=event.command.get('writeConcern'),
                  has_session='lsid' in event.command)

    def succeeded(self, event):
        self.emit('command_succeeded', command=event.command_name,
                  request_id=event.request_id, node=event.connection_id,
                  operation_time=str(event.reply.get('operationTime', '')),
                  snapshot_time=str(event.reply.get('cursor', {}).get('atClusterTime', '')),
                  duration_micros=event.duration_micros)

    def failed(self, event):
        self.emit('command_failed', command=event.command_name,
                  request_id=event.request_id, node=event.connection_id,
                  duration_micros=event.duration_micros, failure=event.failure)


def topology(direct):
    states = []
    for host, client in direct:
        try:
            hello = client.admin.command('hello')
            states.append({'host': host, 'member': hello.get('me'),
                           'primary': bool(hello.get('isWritablePrimary')),
                           'secondary': bool(hello.get('secondary')),
                           'set_name': hello.get('setName')})
        except PyMongoError as exc:
            states.append({'host': host, 'error': type(exc).__name__ + ': ' + str(exc)})
    return states


def wait_ready(direct, timeout):
    deadline = time.monotonic() + timeout
    while True:
        states = topology(direct)
        names = {s.get('member') for s in states}
        sets = {s.get('set_name') for s in states}
        if (len(states) == 3 and len(names) == 3 and None not in names
                and len(sets) == 1 and None not in sets
                and sum(s.get('primary', False) for s in states) == 1
                and sum(s.get('secondary', False) for s in states) == 2):
            return states
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Replica set is not healthy (need 1 primary + 2 secondaries): {states}')
        time.sleep(.25)


class Workload:
    def __init__(self, args, client, producer, observer, session, trace, fault, collection_name):
        self.args, self.client, self.observer = args, client, observer
        self.session, self.trace, self.fault = session, trace, fault
        wc, rc, _ = PROFILES[args.profile]
        options = dict(write_concern=WriteConcern(w=wc, wtimeout=args.timeout_ms),
                       read_concern=ReadConcern(rc), read_preference=ReadPreference.PRIMARY)
        self.main = client[args.database].get_collection(collection_name, **options)
        self.secondary = self.main.with_options(read_preference=ReadPreference.SECONDARY)
        self.producer = producer[args.database].get_collection(collection_name, **options)
        # Fixed measurement policy for BOTH profiles; no application-session clock is shared.
        self.snapshot = observer[args.database].get_collection(
            collection_name, read_concern=ReadConcern('snapshot'),
            read_preference=ReadPreference.SECONDARY)
        self.max_seen = -1
        self.last_ack = None
        self.management_ms = 0

    def boundary(self, iteration):
        if self.args.scenario != 'normal' and iteration == self.args.fault_at:
            self.trace.stage = 'fault_injection'
            hello = self.client.admin.command('hello')
            primary = hello.get('primary')
            if not primary:
                raise FaultError('No primary was found at the dependency boundary.')
            started = time.perf_counter()
            try:
                self.fault.inject(primary.rsplit(':', 1)[0])
            finally:
                self.management_ms += (time.perf_counter() - started) * 1000

    def observe(self, ids, dependent_id):
        deadline = time.monotonic() + self.args.observe_timeout_ms / 1000
        attempts = 0
        while True:
            attempts += 1
            # A NEW snapshot per poll. Reusing a snapshot would never see replication progress.
            with self.observer.start_session(snapshot=True, causal_consistency=False) as observation:
                docs = list(self.snapshot.find({'_id': {'$in': ids}},
                            {'_id': 1, 'depends_on': 1}, session=observation,
                            max_time_ms=self.args.timeout_ms))
            if any(doc['_id'] == dependent_id for doc in docs) or time.monotonic() >= deadline:
                return docs, attempts
            time.sleep(.01)

    def step(self, i, detail):
        model = self.args.model
        self.trace.stage = 'first_write'
        if model == 'ryw':
            # Immutable per-iteration IDs avoid update matched_count / initialization ambiguity.
            identity = f'ryw:{i}'
            self.main.insert_one({'_id': identity, 'version': i}, session=self.session)
            detail['write_acknowledged'] = True
            self.boundary(i)
            self.trace.stage = 'read_own_write'
            found = self.secondary.find_one({'_id': identity}, session=self.session,
                                           max_time_ms=self.args.timeout_ms)
            detail['observed_version'] = found['version'] if found else None
            return 'pass' if found and found['version'] == i else 'violation'
        if model == 'wfr':
            prerequisite, dependent = f'v1:{i}', f'v2:{i}'
            # A uses a separate MongoClient, and never shares B's session.
            self.producer.insert_one({'_id': prerequisite, 'writer': 'A'})
            detail['prerequisite_acknowledged'] = True
            self.trace.stage = 'client_b_read'
            found = self.main.find_one({'_id': prerequisite}, session=self.session,
                                      max_time_ms=self.args.timeout_ms)
            detail['client_b_read_v1'] = found is not None
            if found is None:
                return 'not_observed'
            self.boundary(i)
            self.trace.stage = 'client_b_write'
            self.main.insert_one({'_id': dependent, 'writer': 'B', 'depends_on': prerequisite},
                                 session=self.session)
            detail['dependent_acknowledged'] = True
            self.trace.stage = 'independent_snapshot'
            docs, polls = self.observe([prerequisite, dependent], dependent)
            detail.update(visible_ids=[d['_id'] for d in docs], snapshot_polls=polls)
            return dependency_verdict(docs, prerequisite, dependent)
        if model == 'mr':
            self.producer.update_one({'_id': 'register'}, {'$set': {'version': i}})
            detail['background_write_acknowledged'] = True
            violation = False
            detail['reads'] = []
            for index, reader in enumerate((self.main, self.secondary)):
                self.trace.stage = 'read_primary' if index == 0 else 'read_secondary'
                doc = reader.find_one({'_id': 'register'}, session=self.session,
                                      max_time_ms=self.args.timeout_ms)
                version = doc['version'] if doc else -1
                regression = version < self.max_seen
                detail['reads'].append({'version': version, 'max_seen_before': self.max_seen,
                                        'violation': regression})
                violation |= regression
                self.max_seen = max(self.max_seen, version)
                if index == 0:
                    self.boundary(i)
            return 'violation' if violation else 'pass'
        # MW: dependencies refer ONLY to acknowledged writes, never assumed sequence numbers.
        first, second, previous = f'w:{2*i-1}', f'w:{2*i}', self.last_ack
        self.main.insert_one({'_id': first, 'depends_on': previous}, session=self.session)
        self.last_ack = first
        detail['first_acknowledged'] = True
        self.boundary(i)
        self.trace.stage = 'second_write'
        self.main.insert_one({'_id': second, 'depends_on': first}, session=self.session)
        self.last_ack = second
        detail['second_acknowledged'] = True
        self.trace.stage = 'independent_snapshot'
        ids = [first, second] + ([previous] if previous else [])
        docs, polls = self.observe(ids, second)
        detail.update(visible_ids=[d['_id'] for d in docs], previous_ack=previous, snapshot_polls=polls)
        detail['visible_dependencies'] = [d for d in docs if d['_id'] in (first, second)]
        return monotonic_write_verdict(docs, first, second)


def execute(args):
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ_') + uuid.uuid4().hex[:8]
    folder = args.output.resolve() / f'{run_id}_{args.model}_{args.scenario}_{args.profile}'
    folder.mkdir(parents=True, exist_ok=False)
    trace = Trace(folder / 'events.jsonl.gz')
    rows, fatal, recovery_error = [], '', ''
    fault = FaultController(args.scenario, [s.strip() for s in args.containers.split(',')],
                            trace.emit, args.network)
    collection_name = f'{args.model}_{run_id}'
    metadata = {'schema_version': 3, 'run_id': run_id, 'model': args.model, 'scenario': args.scenario,
                'profile': args.profile, 'write_concern': PROFILES[args.profile][0],
                'read_concern': PROFILES[args.profile][1],
                'causal_session': PROFILES[args.profile][2],
                'observer_read_concern': 'snapshot' if args.model in ('wfr', 'mw') else PROFILES[args.profile][1],
                'database': args.database, 'collection': collection_name,
                'python': sys.version, 'pymongo': pymongo.version, 'platform': platform.platform(),
                'iterations': args.iterations, 'fault_at': args.fault_at, 'recover_at': args.recover_at,
                'timeout_ms': args.timeout_ms, 'observe_timeout_ms': args.observe_timeout_ms,
                'interval_ms': args.interval_ms, 'retry_reads': False, 'retry_writes': False,
                'status': 'running', 'fault_position': 'between_dependent_operations',
                'measurement': 'committed_dependency_visibility' if args.model in ('wfr', 'mw') else 'application_reads'}
    metadata['direct_hosts'] = args.direct_hosts.split(',')
    metadata['application_read_route'] = 'primary_then_secondary' if args.model == 'mr' else (
        'secondary' if args.model == 'ryw' else 'primary')
    metadata['observer_read_route'] = 'secondary' if args.model in ('wfr', 'mw') else None
    metadata['fault_mechanism'] = {'normal': None, 'failure': 'docker_stop_graceful_5s',
                                 'partition': 'docker_network_disconnect_primary'}[args.scenario]
    metadata['source_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in Path(__file__).parent.glob('*.py')}
    write_json(folder / 'metadata.json', metadata)
    print(f'Output: {folder}', flush=True)
    fields = ['run_id', 'iteration', 'phase', 'start_time', 'end_time', 'latency_ms',
              'management_ms', 'workload_ms',
              'verdict', 'error', 'last_stage', 'details_json']
    with ExitStack() as stack:
        log = stack.enter_context((folder / 'operations.csv').open('w', encoding='utf-8', newline=''))
        writer = csv.DictWriter(log, fieldnames=fields)
        writer.writeheader()
        def connect(uri, **kwargs):
            client = MongoClient(uri, serverSelectionTimeoutMS=args.timeout_ms,
                                 connectTimeoutMS=args.timeout_ms, socketTimeoutMS=args.timeout_ms * 2,
                                 retryReads=False, retryWrites=False, event_listeners=[trace], **kwargs)
            stack.callback(client.close)
            return client
        direct = []
        try:
            client, producer, observer = (connect(args.uri) for _ in range(3))
            direct = [(host.strip(), connect(f'mongodb://{host.strip()}/', directConnection=True))
                      for host in args.direct_hosts.split(',')]
            initial = wait_ready(direct, args.ready_timeout)
            metadata['initial_topology'] = initial
            # Ensure direct hosts actually refer to this driver's replica set before mutating anything.
            hello = client.admin.command('hello')
            if {s['member'] for s in initial} != set(hello.get('hosts', [])):
                raise RuntimeError('--uri and --direct-hosts do not identify the same three members.')
            metadata['mongodb'] = client.admin.command('buildInfo')['version']
            metadata['member_versions'] = {host: member.admin.command('buildInfo')['version']
                                          for host, member in direct}
            metadata['replica_config'] = client.admin.command('replSetGetConfig')['config']
            fault.preflight()
            metadata['network'] = fault.network
            # Fresh collection per run; no delete/drop against existing project data.
            setup = client[args.database].get_collection(collection_name,
                    write_concern=WriteConcern(w='majority', wtimeout=args.timeout_ms))
            setup.insert_one({'_id': 'register' if args.model == 'mr' else '__run__', 'version': 0})
            # Every baseline has an explicit session, with causal consistency explicitly selected.
            with client.start_session(causal_consistency=PROFILES[args.profile][2]) as session:
                workload = Workload(args, client, producer, observer, session, trace, fault, collection_name)
                for i in range(1, args.iterations + 1):
                    trace.iteration, trace.stage = i, 'iteration_start'
                    if args.scenario != 'normal' and i == args.recover_at:
                        fault.restore()
                    phase = ('normal' if args.scenario == 'normal' or i < args.fault_at else
                             'transition' if i == args.fault_at else
                             'fault' if i < args.recover_at else 'recovery')
                    row = dict(run_id=run_id, iteration=i, phase=phase, start_time=time.time(),
                               verdict='error', error='')
                    started, detail = time.perf_counter(), {}
                    workload.management_ms = 0
                    try:
                        row['verdict'] = workload.step(i, detail)
                    except PyMongoError as exc:
                        row['error'] = type(exc).__name__ + ': ' + str(exc)
                        detail['error_type'] = type(exc).__name__
                        detail['error_code'] = getattr(exc, 'code', None)
                    except BaseException as exc:
                        row['error'] = type(exc).__name__ + ': ' + str(exc)
                        raise
                    finally:
                        total_ms = (time.perf_counter()-started)*1000
                        row.update(end_time=time.time(), latency_ms=round(total_ms, 3),
                                   management_ms=round(workload.management_ms, 3),
                                   workload_ms=round(max(0, total_ms-workload.management_ms), 3),
                                   last_stage=trace.stage, details_json=json.dumps(detail, ensure_ascii=False))
                        rows.append(row)
                        writer.writerow(row)
                        log.flush()
                    if i == args.fault_at and args.scenario != 'normal' and not fault.injected:
                        raise FaultError('Dependency boundary was not reached; fault run is invalid.')
                    if i % 100 == 0:
                        print(f'{i}/{args.iterations}: {summarize(rows)}', flush=True)
                    if args.interval_ms:
                        time.sleep(args.interval_ms / 1000)
        except (Exception, KeyboardInterrupt) as exc:
            fatal = type(exc).__name__ + ': ' + str(exc)
            trace.emit('run_error', error=fatal)
        finally:
            trace.stage = 'final_recovery'
            try:
                fault.restore()
                if direct:
                    metadata['final_topology'] = wait_ready(direct, args.ready_timeout)
            except (Exception, KeyboardInterrupt) as exc:
                recovery_error = type(exc).__name__ + ': ' + str(exc)
                trace.emit('recovery_error', error=recovery_error, target=fault.target, network=fault.network)
            valid = (not fatal and not recovery_error and len(rows) == args.iterations and
                     (args.scenario == 'normal' or (fault.injected and fault.restored)))
            metadata.update(status='valid' if valid else 'invalid', fatal_error=fatal,
                            recovery_error=recovery_error, fault_target=fault.target,
                            fault_injected=fault.injected, fault_restored=fault.restored)
            write_json(folder / 'metadata.json', metadata)
            summary = {'run_valid': valid, 'overall': summarize(rows),
                       'by_phase': {phase: summarize([r for r in rows if r['phase'] == phase])
                                    for phase in dict.fromkeys(r['phase'] for r in rows)}}
            write_json(folder / 'summary.json', summary)
    trace.stream.close()
    print(json.dumps(summary, indent=2), flush=True)
    if fatal or recovery_error:
        print(f'INVALID RUN: {fatal} {recovery_error}', file=sys.stderr)
    # Zero eligible observations must never look like a passing verification.
    return 0 if valid and summary['overall']['eligible'] else 2


def main():
    return execute(parse_args())


if __name__ == '__main__':
    raise SystemExit(main())
