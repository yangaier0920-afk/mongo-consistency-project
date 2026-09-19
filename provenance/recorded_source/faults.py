"""Checked Docker faults, discovered network names, and idempotent recovery."""
import json
import subprocess


class FaultError(RuntimeError):
    pass


def docker(*args):
    try:
        completed = subprocess.run(
            ['docker', *args], check=True, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=45,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        detail = getattr(exc, 'stderr', '') or str(exc)
        raise FaultError(f"docker {' '.join(args)}: {detail}") from exc
    return completed.stdout


def inspect_container(name):
    return json.loads(docker('inspect', name))[0]


def choose_network(containers, requested=None):
    common = set(containers[0]['NetworkSettings']['Networks'])
    for item in containers[1:]:
        common.intersection_update(item['NetworkSettings']['Networks'])
    if requested:
        if requested not in common:
            raise FaultError(f'Network {requested!r} is not shared by all replica containers.')
        return requested
    if len(common) != 1:
        raise FaultError(f'Expected one common Docker network, got {sorted(common)}. Use --network.')
    return next(iter(common))


class FaultController:
    def __init__(self, scenario, containers, emit, network=None):
        self.scenario, self.containers, self.emit = scenario, containers, emit
        self.network = network
        self.target = None
        self.aliases = []
        self.active = False
        self.injected = False
        self.restored = False
        self.initial = []

    def preflight(self):
        if self.scenario == 'normal':
            return
        self.initial = [inspect_container(n) for n in self.containers]
        if not all(c['State']['Running'] for c in self.initial):
            raise FaultError('All replica containers must be running before a fault run.')
        if self.scenario == 'partition':
            self.network = choose_network(self.initial, self.network)
        self.emit('fault_preflight', network=self.network, containers=self.containers,
                  deployment=[{'name': c['Name'], 'image': c.get('Image'),
                               'networks': list(c['NetworkSettings']['Networks'])} for c in self.initial])

    def inject(self, primary_host):
        matches = [c for c in self.initial if primary_host in (
            c['Name'].lstrip('/'), c['Config']['Hostname'],
        )]
        if len(matches) != 1:
            raise FaultError(f'Cannot map primary {primary_host!r} to an inspected container.')
        self.target = matches[0]['Name'].lstrip('/')
        current = inspect_container(self.target)
        if self.scenario == 'partition':
            endpoint = current['NetworkSettings']['Networks'].get(self.network)
            if endpoint is None:
                raise FaultError('Target was already disconnected before injection.')
            self.aliases = endpoint.get('Aliases') or []
        # Set active BEFORE mutation: recovery is needed even if the command times out.
        self.active = True
        self.emit('fault_begin', target=self.target, network=self.network)
        if self.scenario == 'failure':
            docker('stop', '--time', '5', self.target)
            if inspect_container(self.target)['State']['Running']:
                raise FaultError('Stop command returned but target is still running.')
        else:
            docker('network', 'disconnect', self.network, self.target)
            current = inspect_container(self.target)
            if not current['State']['Running'] or self.network in current['NetworkSettings']['Networks']:
                raise FaultError('Partition did not leave a running, disconnected container.')
        self.injected = True
        proof = inspect_container(self.target)
        self.emit('fault_confirmed', target=self.target, network=self.network,
                  running=proof['State']['Running'], networks=list(proof['NetworkSettings']['Networks']))

    def restore(self):
        if not self.active:
            return
        self.emit('recovery_begin', target=self.target, network=self.network)
        current = inspect_container(self.target)
        if self.scenario == 'failure':
            if not current['State']['Running']:
                docker('start', self.target)
            if not inspect_container(self.target)['State']['Running']:
                raise FaultError('Container did not restart.')
        else:
            if self.network not in current['NetworkSettings']['Networks']:
                args = ['network', 'connect']
                for alias in self.aliases:
                    args.extend(['--alias', alias])
                docker(*args, self.network, self.target)
            if self.network not in inspect_container(self.target)['NetworkSettings']['Networks']:
                raise FaultError('Network reconnect could not be verified.')
        self.active = False
        self.restored = True
        proof = inspect_container(self.target)
        self.emit('recovery_confirmed', target=self.target, network=self.network,
                  running=proof['State']['Running'], networks=list(proof['NetworkSettings']['Networks']))
