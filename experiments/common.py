import csv
import os
import subprocess
import time

from pymongo import MongoClient, ReadPreference, WriteConcern
from pymongo.read_concern import ReadConcern


DEFAULT_URI = "mongodb://localhost:27017,localhost:27018,localhost:27019/?replicaSet=rs0"
DEFAULT_DIRECT_HOSTS = "localhost:27017,localhost:27018,localhost:27019"
DEFAULT_DB = "dsa5208_db"
DEFAULT_DOCKER_NETWORK = "mongo-consistency-project_mongodb-network"


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


def resolve_docker_network(requested_network=None):
    if requested_network:
        return requested_network

    result = subprocess.run(
        ["docker", "network", "ls", "--format", "{{.Name}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    networks = [line.strip() for line in result.stdout.splitlines() if line.strip()]

    if DEFAULT_DOCKER_NETWORK in networks:
        return DEFAULT_DOCKER_NETWORK

    candidates = [name for name in networks if name.endswith("_mongodb-network")]
    if len(candidates) == 1:
        return candidates[0]

    if candidates:
        raise RuntimeError(
            "Multiple MongoDB Docker networks found. Pass --network explicitly: "
            + ", ".join(candidates)
        )

    raise RuntimeError(
        "Cannot find a Docker network ending with '_mongodb-network'. "
        "Start the stack with docker-compose up -d or pass --network."
    )


def docker_network_contains(network_name, container):
    result = subprocess.run(
        ["docker", "network", "inspect", network_name],
        capture_output=True,
        text=True,
        check=True,
    )
    return container in result.stdout


def observe_dependency(observer_collection, prerequisite_id, dependent_id, session=None):
    observed_v2 = observer_collection.find_one(
        {"_id": dependent_id},
        session=session,
    )
    observed_v1 = observer_collection.find_one(
        {"_id": prerequisite_id},
        session=session,
    )
    return observed_v1, observed_v2, "dependent_then_prerequisite"


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
