"""Initialize only an uninitialized replica set, then verify all three members."""
import argparse
from pymongo import MongoClient
from pymongo.errors import OperationFailure
from .run import wait_ready


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--members', default='mongo1:27017,mongo2:27018,mongo3:27019')
    parser.add_argument('--direct-hosts', default='localhost:27017,localhost:27018,localhost:27019')
    parser.add_argument('--replica-set', default='rs0')
    parser.add_argument('--timeout', type=float, default=90)
    args = parser.parse_args()
    members, hosts = args.members.split(','), args.direct_hosts.split(',')
    if len(set(members)) != 3 or len(set(hosts)) != 3:
        parser.error('Exactly three distinct members and direct hosts are required')
    direct = [(host, MongoClient('mongodb://' + host, directConnection=True,
               serverSelectionTimeoutMS=3000, socketTimeoutMS=5000)) for host in hosts]
    try:
        first = direct[0][1]
        try:
            config = first.admin.command('replSetGetConfig')['config']
        except OperationFailure as exc:
            if exc.code != 94:  # NotYetInitialized; other failures must not look successful.
                raise
            first.admin.command('replSetInitiate', {'_id': args.replica_set,
                                'members': [{'_id': i, 'host': h} for i, h in enumerate(members)]})
        else:
            if config['_id'] != args.replica_set or {m['host'] for m in config['members']} != set(members):
                raise RuntimeError('Existing replica configuration differs; no reconfiguration performed')
        print(wait_ready(direct, args.timeout))
    finally:
        for _, client in direct:
            client.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
