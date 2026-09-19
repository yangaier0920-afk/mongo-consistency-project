"""Offline regression tests: python -m unittest experiments_v2.test_v2 -v."""
import subprocess
import json
import tempfile
import unittest
import gzip
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from .checks import dependency_verdict, monotonic_write_verdict, summarize
from .faults import FaultController, FaultError, choose_network, docker
from .run import PROFILES, Trace, Workload, execute, parse_args


def container(name='mongo1', connected=True, running=True):
    return {'Name': '/' + name, 'Config': {'Hostname': name},
            'State': {'Running': running},
            'NetworkSettings': {'Networks': {'folder-main_mongodb-network':
                {'Aliases': [name, 'saved-alias']}} if connected else {}}}


class VerdictTests(unittest.TestCase):
    def test_valid_dependency(self):
        self.assertEqual(dependency_verdict([{'_id': 'a'}, {'_id': 'b'}], 'a', 'b'), 'pass')

    def test_missing_dependency_in_one_snapshot(self):
        self.assertEqual(dependency_verdict([{'_id': 'b'}], 'a', 'b'), 'violation')

    def test_invisible_dependent_is_not_a_pass(self):
        for docs in ([], [{'_id': 'a'}]):
            self.assertEqual(dependency_verdict(docs, 'a', 'b'), 'not_observed')

    def test_denominator_excludes_errors_and_unobserved(self):
        rows = [{'verdict': v, 'latency_ms': 1} for v in ['pass', 'violation', 'error', 'not_observed']]
        summary = summarize(rows)
        self.assertEqual(summary['eligible'], 2)
        self.assertEqual(summary['violation_rate'], .5)
        self.assertEqual(summary['error_rate'], .25)

    def test_no_observations_has_no_rate(self):
        self.assertIsNone(summarize([])['violation_rate'])
        self.assertIsNone(summarize([{'verdict': 'error', 'latency_ms': 1}])['violation_rate'])

    def test_first_write_alone_does_not_test_order(self):
        self.assertEqual(monotonic_write_verdict([{'_id': 'a', 'depends_on': None}], 'a', 'b'), 'not_observed')

    def test_partial_mr_error_preserves_observed_regression(self):
        summary = summarize([{'verdict': 'error', 'latency_ms': 1,
                              'details_json': json.dumps({'reads': [{'violation': True}]})}])
        self.assertEqual(summary['eligible'], 0)
        self.assertEqual(summary['mr_read_regressions'], 1)


class FaultTests(unittest.TestCase):
    def test_network_name_is_discovered_even_with_main_suffix(self):
        self.assertEqual(choose_network([container(n) for n in ['mongo1','mongo2','mongo3']]),
                         'folder-main_mongodb-network')

    def test_wrong_explicit_network_rejected(self):
        with self.assertRaises(FaultError):
            choose_network([container()], 'old-project_mongodb-network')

    def test_ambiguous_network_rejected(self):
        item = container()
        item['NetworkSettings']['Networks']['extra'] = {}
        with self.assertRaises(FaultError):
            choose_network([item])

    @patch('experiments_v2.faults.inspect_container')
    @patch('experiments_v2.faults.docker')
    def test_disconnect_error_never_marks_injection_successful(self, command, inspect):
        ctl = FaultController('partition', ['mongo1'], MagicMock())
        ctl.initial, ctl.network = [container()], 'folder-main_mongodb-network'
        inspect.return_value = container()
        command.side_effect = FaultError('disconnect failed')
        with self.assertRaises(FaultError):
            ctl.inject('mongo1')
        self.assertFalse(ctl.injected)
        self.assertTrue(ctl.active)  # command might have taken effect before an uncertain failure
        command.side_effect = None
        ctl.restore()  # state inspection says already connected; no destructive extra change
        self.assertFalse(ctl.active)

    @patch('experiments_v2.faults.inspect_container')
    @patch('experiments_v2.faults.docker')
    def test_partition_reconnect_preserves_aliases_and_is_idempotent(self, command, inspect):
        ctl = FaultController('partition', ['mongo1'], MagicMock())
        ctl.target, ctl.network = 'mongo1', 'folder-main_mongodb-network'
        ctl.aliases, ctl.active = ['mongo1', 'saved-alias'], True
        inspect.side_effect = [container(connected=False), container(), container()]
        ctl.restore()
        ctl.restore()
        command.assert_called_once_with('network', 'connect', '--alias', 'mongo1',
                                        '--alias', 'saved-alias', ctl.network, 'mongo1')
        self.assertTrue(ctl.restored)

    @patch('experiments_v2.faults.subprocess.run')
    def test_docker_exit_failure_propagates(self, run):
        run.side_effect = subprocess.CalledProcessError(1, ['docker'], stderr='network not found')
        with self.assertRaisesRegex(FaultError, 'network not found'):
            docker('network', 'disconnect', 'missing', 'mongo1')

    @patch('experiments_v2.faults.inspect_container')
    @patch('experiments_v2.faults.docker')
    def test_successful_command_with_wrong_state_is_rejected(self, command, inspect):
        ctl = FaultController('partition', ['mongo1'], MagicMock())
        ctl.initial, ctl.network = [container()], 'folder-main_mongodb-network'
        inspect.return_value = container()  # remains connected despite command success
        with self.assertRaisesRegex(FaultError, 'Partition did not'):
            ctl.inject('mongo1')
        self.assertFalse(ctl.injected)


class WorkloadTests(unittest.TestCase):
    def workload(self, model='wfr'):
        args = parse_args(['--model', model, '--profile', 'causal'])
        client, producer, observer, session, trace, fault = [MagicMock() for _ in range(6)]
        work = Workload(args, client, producer, observer, session, trace, fault, 'test')
        return work

    def test_ryw_write_and_read_share_application_session(self):
        work = self.workload('ryw')
        work.secondary.find_one.return_value = {'_id': 'ryw:1', 'version': 1}
        self.assertEqual(work.step(1, {}), 'pass')
        self.assertIs(work.main.insert_one.call_args.kwargs['session'], work.session)
        self.assertIs(work.secondary.find_one.call_args.kwargs['session'], work.session)

    def test_ryw_stale_read_is_a_violation(self):
        work = self.workload('ryw')
        work.secondary.find_one.return_value = None
        self.assertEqual(work.step(1, {}), 'violation')

    def test_wfr_catchup_cannot_merge_different_snapshots(self):
        work = self.workload()
        work.main.find_one.return_value = {'_id': 'v1:1'}
        work.snapshot.find.side_effect = [[], [{'_id': 'v1:1'}, {'_id': 'v2:1'}]]
        detail = {}
        self.assertEqual(work.step(1, detail), 'pass')
        self.assertEqual(detail['snapshot_polls'], 2)
        self.assertEqual(work.observer.start_session.call_count, 2)
        work.observer.start_session.assert_called_with(snapshot=True, causal_consistency=False)
        # A does not receive B's session. B read and write do share that session.
        self.assertNotIn('session', work.producer.insert_one.call_args.kwargs)
        self.assertIs(work.main.find_one.call_args.kwargs['session'], work.session)
        self.assertIs(work.main.insert_one.call_args.kwargs['session'], work.session)
        self.assertIsNot(work.snapshot.find.call_args.kwargs['session'], work.session)

    def test_wfr_actual_missing_dependency_is_detected(self):
        work = self.workload()
        work.snapshot.find.return_value = [{'_id': 'v2:1'}]
        self.assertEqual(work.step(1, {}), 'violation')

    def test_wfr_requires_client_b_to_observe_prerequisite(self):
        work = self.workload()
        work.main.find_one.return_value = None
        self.assertEqual(work.step(1, {}), 'not_observed')
        work.main.insert_one.assert_not_called()

    def test_mw_missing_attempted_sequence_is_not_an_assumed_dependency(self):
        work = self.workload('mw')
        work.last_ack = 'w:1'  # attempts 2..9 did not acknowledge; do not demand their presence
        work.snapshot.find.return_value = [
            {'_id': 'w:1'}, {'_id': 'w:11', 'depends_on': 'w:1'},
            {'_id': 'w:12', 'depends_on': 'w:11'}]
        self.assertEqual(work.step(6, {}), 'pass')

    def test_mw_missing_acknowledged_predecessor_is_detected(self):
        work = self.workload('mw')
        work.last_ack = 'w:1'
        work.snapshot.find.return_value = [
            {'_id': 'w:3', 'depends_on': 'w:1'}, {'_id': 'w:4', 'depends_on': 'w:3'}]
        self.assertEqual(work.step(2, {}), 'violation')

    def test_mr_remembers_reads_across_iterations(self):
        work = self.workload('mr')
        work.max_seen = 10
        work.main.find_one.return_value = {'version': 9}
        work.secondary.find_one.return_value = {'version': 10}
        self.assertEqual(work.step(11, {}), 'violation')

    def test_short_fault_run_requires_explicit_schedule(self):
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            parse_args(['--model', 'ryw', '--scenario', 'partition', '--iterations', '30'])

    def test_all_profiles_are_explicit(self):
        self.assertEqual(PROFILES['weak'], (1, 'local', False))
        self.assertEqual(PROFILES['majority'], ('majority', 'majority', False))
        self.assertEqual(PROFILES['causal'], ('majority', 'majority', True))


class RunnerFailureTests(unittest.TestCase):
    def test_command_trace_preserves_concerns_nodes_and_snapshot_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'events.jsonl.gz'
            trace = Trace(path)
            trace.started(SimpleNamespace(command_name='find', request_id=1, connection_id=('mongo2', 27018),
                                          command={'readConcern': {'level': 'snapshot'}, 'lsid': {}}))
            trace.succeeded(SimpleNamespace(command_name='find', request_id=1, connection_id=('mongo2', 27018),
                                            reply={'cursor': {'atClusterTime': 'test-time'}}, duration_micros=12))
            trace.stream.close()
            with gzip.open(path, 'rt', encoding='utf-8') as stream:
                events = [json.loads(line) for line in stream]
            self.assertEqual(events[0]['read_concern'], {'level': 'snapshot'})
            self.assertEqual(events[0]['node'], ['mongo2', 27018])
            self.assertEqual(events[1]['snapshot_time'], 'test-time')

    def test_mid_run_fault_error_preserves_evidence_and_attempts_recovery(self):
        states = [{'member': f'mongo{i}:{27016+i}', 'set_name': 'rs0',
                   'primary': i == 1, 'secondary': i != 1} for i in (1, 2, 3)]
        with tempfile.TemporaryDirectory() as directory:
            args = parse_args(['--model', 'wfr', '--scenario', 'partition', '--iterations', '2',
                               '--fault-at', '1', '--recover-at', '2', '--output', directory])
            with patch('experiments_v2.run.MongoClient') as mongo, \
                 patch('experiments_v2.run.wait_ready', return_value=states), \
                 patch('experiments_v2.run.FaultController') as factory, \
                 patch('experiments_v2.run.Workload') as workload, \
                 patch('builtins.print'):
                mongo.return_value.admin.command.return_value = {
                    'hosts': [s['member'] for s in states], 'version': '8.0', 'config': {}}
                factory.return_value.injected = False
                factory.return_value.restored = False
                workload.return_value.step.side_effect = FaultError('injection failed')
                self.assertEqual(execute(args), 2)
                factory.return_value.restore.assert_called_once()
            folder = next(Path(directory).iterdir())
            metadata = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
            summary = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['status'], 'invalid')
            self.assertIn('injection failed', metadata['fatal_error'])
            self.assertEqual(summary['overall']['errors'], 1)
            self.assertFalse(summary['run_valid'])
            self.assertIn('injection failed', (folder / 'operations.csv').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
