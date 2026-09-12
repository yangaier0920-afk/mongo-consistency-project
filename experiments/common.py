import csv
import os
import time

from pymongo import MongoClient, ReadPreference, WriteConcern
from pymongo.read_concern import ReadConcern


DEFAULT_URI = "mongodb://localhost:27017,localhost:27018,localhost:27019/?replicaSet=rs0"
DEFAULT_DIRECT_HOSTS = "localhost:27017,localhost:27018,localhost:27019"
DEFAULT_DB = "dsa5208_db"


def parse_write_concern(value):
    return int(value) if str(value).isdigit() else value


def build_write_concern(value, wtimeout_ms=5000):
    return WriteConcern(w=parse_write_concern(value), wtimeout=wtimeout_ms)


def build_read_concern(value):
    return ReadConcern(level=value)


def replica_client(uri=DEFAULT_URI, timeout_ms=5000):
    return MongoClient(
        uri,
        serverSelectionTimeoutMS=timeout_ms,
        connectTimeoutMS=timeout_ms,
        socketTimeoutMS=timeout_ms * 2,
    )


def direct_client(host, timeout_ms=5000):
    return MongoClient(
        f"mongodb://{host}/?directConnection=true",
        serverSelectionTimeoutMS=timeout_ms,
        connectTimeoutMS=timeout_ms,
        socketTimeoutMS=timeout_ms * 2,
    )


def parse_direct_hosts(value):
    return [host.strip() for host in value.split(",") if host.strip()]


def discover_members(direct_hosts, timeout_s=20):
    deadline = time.time() + timeout_s
    last_error = None

    while time.time() < deadline:
        members = []

        for host in direct_hosts:
            client = direct_client(host)
            try:
                hello = client.admin.command("hello")
                members.append(
                    {
                        "host": host,
                        "is_primary": bool(hello.get("isWritablePrimary")),
                        "is_secondary": bool(hello.get("secondary")),
                    }
                )
            except Exception as exc:
                last_error = exc
            finally:
                client.close()

        if any(member["is_primary"] for member in members) and any(
            member["is_secondary"] for member in members
        ):
            return members

        time.sleep(1)

    raise RuntimeError(
        "Replica set is not ready. Start MongoDB on ports 27017-27019 and run "
        f"setup/init_replica.py first. Last error: {last_error}"
    )


def secondary_hosts(members):
    return [member["host"] for member in members if member["is_secondary"]]


def collection_for_client(
    client,
    db_name,
    collection_name,
    write_concern=None,
    read_concern=None,
    read_preference=None,
):
    db = client.get_database(
        db_name,
        write_concern=write_concern,
        read_concern=read_concern,
    )
    return db.get_collection(
        collection_name,
        write_concern=write_concern,
        read_concern=read_concern,
        read_preference=read_preference,
    )


def collection_for_direct_host(
    host,
    db_name,
    collection_name,
    read_concern=None,
    read_preference=ReadPreference.SECONDARY,
):
    client = direct_client(host)
    collection = collection_for_client(
        client,
        db_name,
        collection_name,
        read_concern=read_concern,
        read_preference=read_preference,
    )
    return client, collection


def reset_collection(client, db_name, collection_name, initial_docs=None):
    collection = collection_for_client(
        client,
        db_name,
        collection_name,
        write_concern=WriteConcern(w="majority", wtimeout=10000),
        read_preference=ReadPreference.PRIMARY,
    )
    collection.delete_many({})
    if initial_docs:
        collection.insert_many(initial_docs)
    time.sleep(0.5)


def save_csv(rows, filename):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    if not rows:
        raise ValueError("No experiment rows were generated.")

    with open(filename, mode="w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
