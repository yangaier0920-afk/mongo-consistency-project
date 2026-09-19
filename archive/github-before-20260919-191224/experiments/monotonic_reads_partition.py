import argparse
import subprocess
import time
from contextlib import nullcontext

from pymongo import ReadPreference

from common import (
    DEFAULT_DB,
    DEFAULT_DIRECT_HOSTS,
    DEFAULT_URI,
    build_read_concern,
    build_write_concern,
    collection_for_client,
    collection_for_direct_host,
    discover_members,
    docker_network_contains,
    parse_direct_hosts,
    replica_client,
    reset_collection,
    resolve_docker_network,
    save_csv,
    secondary_hosts,
)


NETWORK_NAME = None


def parse_args():
    parser = argparse.ArgumentParser(
        description="MongoDB Monotonic Reads Network Partition Experiment"
    )

    parser.add_argument("--write-concern", type=str, default="1")
    parser.add_argument("--read-concern", type=str, default="local")
    parser.add_argument("--scenario", type=str, default="partition")
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--uri", type=str, default=DEFAULT_URI)
    parser.add_argument("--direct-hosts", type=str, default=DEFAULT_DIRECT_HOSTS)
    parser.add_argument("--database", type=str, default=DEFAULT_DB)
    parser.add_argument("--collection", type=str, default="mr_collection")

    parser.add_argument(
        "--causal-session",
        action="store_true",
    )

    parser.add_argument("--after-write-delay-ms", type=float, default=0)
    parser.add_argument("--between-read-delay-ms", type=float, default=0)
    parser.add_argument(
        "--network",
        default=None,
        help="Docker network name. If omitted, auto-detect *_mongodb-network.",
    )

    return parser.parse_args()


def get_primary_container():
    nodes = [
        ("mongo1", "27017"),
        ("mongo2", "27018"),
        ("mongo3", "27019"),
    ]

    for container, port in nodes:
        try:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "mongosh",
                    "--port",
                    port,
                    "--quiet",
                    "--eval",
                    "print(db.hello().isWritablePrimary)",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.stdout.strip() == "true":
                return container

        except Exception:
            pass

    return None


def disconnect_container(container):
    subprocess.run(
        [
            "docker",
            "network",
            "disconnect",
            "-f",
            NETWORK_NAME,
            container,
        ],
        check=True,
    )


def reconnect_container(container):
    subprocess.run(
        [
            "docker",
            "network",
            "connect",
            NETWORK_NAME,
            container,
        ],
        check=True,
    )


def read_version(collection, session=None):
    doc = collection.find_one(
        {"id": "item1"},
        session=session
    )

    return doc.get("version", -1) if doc else -1


def main():
    global NETWORK_NAME
    args = parse_args()
    NETWORK_NAME = resolve_docker_network(args.network)

    wc = build_write_concern(args.write_concern)
    rc = build_read_concern(args.read_concern)

    client = replica_client(args.uri)
    client.admin.command("ping")

    members = discover_members(
        parse_direct_hosts(args.direct_hosts)
    )

    secondaries = secondary_hosts(members)

    if not secondaries:
        raise RuntimeError(
            "Monotonic Reads requires at least one SECONDARY node."
        )

    reset_collection(
        client,
        args.database,
        args.collection,
        [
            {
                "id": "item1",
                "version": 0,
                "last_writer": "background_writer",
            }
        ],
    )

    writer_collection = collection_for_client(
        client,
        args.database,
        args.collection,
        write_concern=wc,
        read_preference=ReadPreference.PRIMARY,
    )

    primary_reader = collection_for_client(
        client,
        args.database,
        args.collection,
        read_concern=rc,
        read_preference=ReadPreference.PRIMARY,
    )

    session_secondary_reader = collection_for_client(
        client,
        args.database,
        args.collection,
        read_concern=rc,
        read_preference=ReadPreference.SECONDARY,
    )

    direct_secondary_readers = [
        (
            host,
            *collection_for_direct_host(
                host,
                args.database,
                args.collection,
                rc
            )
        )
        for host in secondaries
    ]

    print("========================================")
    print("启动 Monotonic Reads Partition Experiment")
    print("========================================")
    print(f"WriteConcern   = {args.write_concern}")
    print(f"ReadConcern    = {args.read_concern}")
    print(f"Causal Session = {args.causal_session}")
    print(f"Scenario       = {args.scenario}")
    print(f"Iterations     = {args.iterations}")
    print(f"Docker network = {NETWORK_NAME}")
    print("----------------------------------------")

    rows = []
    violations = 0
    failed_operations = 0
    max_seen = -1

    partitioned_node = None

    session_context = (
        client.start_session(causal_consistency=True)
        if args.causal_session
        else nullcontext(None)
    )

    try:
        with session_context as session:

            for i in range(1, args.iterations + 1):

                scenario_event = ""
                target_node = ""
                event_time = ""

                # 第 200 次：隔离当前 Primary
                if args.scenario == "partition" and i == 200:

                    partitioned_node = get_primary_container()

                    if partitioned_node is None:
                        print(
                            f"[Iteration {i}] "
                            "ERROR: Cannot detect PRIMARY."
                        )

                        scenario_event = "primary_detection_failed"
                        event_time = time.time()

                    else:
                        print(
                            f"\n[Iteration {i}] "
                            f"Current PRIMARY: {partitioned_node}"
                        )

                        scenario_event = "primary_network_disconnect"
                        target_node = partitioned_node
                        event_time = time.time()

                        disconnect_container(partitioned_node)

                        print(
                            f"[Iteration {i}] "
                            f"{partitioned_node} network disconnected."
                        )

                        print("Waiting 10 seconds for election...")
                        time.sleep(10)

                        new_primary = get_primary_container()

                        print(
                            f"New PRIMARY: {new_primary}"
                        )

                # 第 600 次：恢复原节点网络
                if args.scenario == "partition" and i == 600:

                    if partitioned_node is not None:

                        scenario_event = "primary_network_reconnect"
                        target_node = partitioned_node
                        event_time = time.time()

                        print(
                            f"\n[Iteration {i}] "
                            f"Reconnecting {partitioned_node}..."
                        )

                        reconnect_container(partitioned_node)

                        print(
                            f"{partitioned_node} network restored."
                        )

                        time.sleep(10)

                        print(
                            f"Current PRIMARY: "
                            f"{get_primary_container()}"
                        )

                if i < 200:
                    phase = "normal"
                elif i < 600:
                    phase = "partition"
                else:
                    phase = "recovery"

                try:
                    writer_collection.update_one(
                        {"id": "item1"},
                        {
                            "$set": {
                                "version": i,
                                "last_writer": "background_writer",
                            }
                        },
                    )

                except Exception as exc:
                    print(
                        f"[ERROR] 第 {i} 次 WRITE 失败: {exc}"
                    )

                if args.after_write_delay_ms > 0:
                    time.sleep(
                        args.after_write_delay_ms / 1000
                    )

                read_targets = [
                    (
                        "READ_PRIMARY",
                        "primary",
                        primary_reader,
                    ),
                    (
                        "READ_SECONDARY",
                        (
                            "session_secondary"
                            if args.causal_session
                            else direct_secondary_readers[
                                (i - 1) %
                                len(direct_secondary_readers)
                            ][0]
                        ),
                        (
                            session_secondary_reader
                            if args.causal_session
                            else direct_secondary_readers[
                                (i - 1) %
                                len(direct_secondary_readers)
                            ][2]
                        ),
                    ),
                ]

                for operation, target, collection in read_targets:

                    start_time = time.time()

                    success = True
                    observed_version = -1
                    is_violation = False
                    error = ""

                    max_seen_before = max_seen

                    try:
                        observed_version = read_version(
                            collection,
                            session=session
                        )

                        is_violation = (
                            observed_version < max_seen_before
                        )

                        if is_violation:
                            violations += 1

                        max_seen = max(
                            max_seen,
                            observed_version
                        )

                    except Exception as exc:
                        success = False
                        failed_operations += 1
                        error = str(exc)

                        print(
                            f"[ERROR] 第 {i} 次 "
                            f"{operation} 失败: {exc}"
                        )

                    latency_ms = (
                        time.time() - start_time
                    ) * 1000

                    rows.append({
                        "timestamp": time.time(),
                        "iteration": i,
                        "client_id": "client_mr",
                        "operation": operation,
                        "target": target,
                        "phase": phase,
                        "written_version": i,
                        "observed_version": observed_version,
                        "max_seen_before": max_seen_before,
                        "read_concern": args.read_concern,
                        "write_concern": args.write_concern,
                        "causal_session": args.causal_session,
                        "scenario": args.scenario,
                        "scenario_event": scenario_event,
                        "target_node": target_node,
                        "event_time": event_time,
                        "latency_ms": round(latency_ms, 2),
                        "success": success,
                        "violation": is_violation,
                        "error": error,
                    })

    finally:

        for _, direct_client, _ in direct_secondary_readers:
            direct_client.close()

        client.close()

        # Safety recovery
        if partitioned_node is not None:
            try:
                if not docker_network_contains(
                    NETWORK_NAME,
                    partitioned_node,
                ):
                    print(
                        f"Safety recovery: reconnecting "
                        f"{partitioned_node}"
                    )

                    reconnect_container(
                        partitioned_node
                    )

            except Exception as exc:
                print(
                    f"WARNING: Safety recovery failed: {exc}"
                )

    violation_rate = (
        violations / len(rows) * 100
        if rows
        else 0
    )

    print("\n========================================")
    print("MR Partition Experiment 完成")
    print("========================================")
    print(f"总读取操作数: {len(rows)}")
    print(f"失败读取操作: {failed_operations}")
    print(
        f"MR violations: "
        f"{violations}/{len(rows)}"
    )
    print(
        f"MR violation rate: "
        f"{violation_rate:.2f}%"
    )

    filename = (
        "results/raw/"
        f"mr_{args.scenario}"
        f"_w{args.write_concern}"
        f"_r{args.read_concern}"
        f"_causal{str(args.causal_session).lower()}.csv"
    )

    save_csv(rows, filename)

    print(f"日志已保存至: {filename}")


if __name__ == "__main__":
    main()
