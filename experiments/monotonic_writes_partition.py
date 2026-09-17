import argparse
import subprocess
import time
from contextlib import nullcontext

from pymongo import ASCENDING, ReadPreference

from common import (
    DEFAULT_DB,
    DEFAULT_DIRECT_HOSTS,
    DEFAULT_URI,
    build_read_concern,
    build_write_concern,
    collection_for_client,
    collection_for_direct_host,
    discover_members,
    parse_direct_hosts,
    replica_client,
    reset_collection,
    save_csv,
    secondary_hosts,
)


NETWORK_NAME = "mongo-consistency-project_mongodb-network"


def parse_args():
    parser = argparse.ArgumentParser(
        description="MongoDB Monotonic Writes Network Partition Experiment"
    )

    parser.add_argument("--write-concern", type=str, default="1")
    parser.add_argument("--read-concern", type=str, default="local")
    parser.add_argument("--scenario", type=str, default="partition")
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--uri", type=str, default=DEFAULT_URI)
    parser.add_argument("--direct-hosts", type=str, default=DEFAULT_DIRECT_HOSTS)
    parser.add_argument("--database", type=str, default=DEFAULT_DB)
    parser.add_argument("--collection", type=str, default="mw_collection")

    parser.add_argument(
        "--causal-session",
        action="store_true",
    )

    parser.add_argument("--between-writes-delay-ms", type=float, default=0)
    parser.add_argument("--after-pair-delay-ms", type=float, default=0)

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


def visible_sequences(collection, upper_bound):
    docs = collection.find(
        {
            "client_id": "client_mw",
            "seq": {"$lte": upper_bound}
        },
        {
            "_id": 0,
            "seq": 1
        },
    )

    return {
        doc["seq"]
        for doc in docs
    }


def find_prefix_gap(sequences):
    if not sequences:
        return 0, []

    max_visible = max(sequences)

    missing = sorted(
        set(range(1, max_visible + 1))
        - sequences
    )

    return max_visible, missing


def main():
    args = parse_args()

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
            "Monotonic Writes requires at least one SECONDARY node."
        )

    reset_collection(
        client,
        args.database,
        args.collection
    )

    writer_collection = collection_for_client(
        client,
        args.database,
        args.collection,
        write_concern=wc,
        read_preference=ReadPreference.PRIMARY,
    )

    writer_collection.create_index(
        [
            ("client_id", ASCENDING),
            ("seq", ASCENDING)
        ]
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
    print("启动 Monotonic Writes Partition Experiment")
    print("========================================")
    print(f"WriteConcern   = {args.write_concern}")
    print(f"ReadConcern    = {args.read_concern}")
    print(f"Causal Session = {args.causal_session}")
    print(f"Scenario       = {args.scenario}")
    print(f"Write pairs    = {args.iterations}")
    print("----------------------------------------")

    rows = []
    violations = 0
    failed_operations = 0

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

                # 第200次隔离 Primary
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

                        disconnect_container(
                            partitioned_node
                        )

                        print(
                            f"{partitioned_node} "
                            f"network disconnected."
                        )

                        time.sleep(10)

                        print(
                            f"New PRIMARY: "
                            f"{get_primary_container()}"
                        )

                # 第600次恢复网络
                if args.scenario == "partition" and i == 600:

                    if partitioned_node is not None:

                        scenario_event = "primary_network_reconnect"
                        target_node = partitioned_node
                        event_time = time.time()

                        print(
                            f"\n[Iteration {i}] "
                            f"Reconnecting {partitioned_node}..."
                        )

                        reconnect_container(
                            partitioned_node
                        )

                        print(
                            f"{partitioned_node} "
                            f"network restored."
                        )

                        time.sleep(10)

                if i < 200:
                    phase = "normal"
                elif i < 600:
                    phase = "partition"
                else:
                    phase = "recovery"

                first_seq = (i * 2) - 1
                second_seq = i * 2

                read_host, _, read_collection = (
                    direct_secondary_readers[
                        (i - 1) %
                        len(direct_secondary_readers)
                    ]
                )

                success = True
                error = ""
                max_visible = -1
                missing = []
                is_violation = False

                start_time = time.time()

                try:
                    writer_collection.insert_one(
                        {
                            "_id": f"client_mw:{first_seq}",
                            "client_id": "client_mw",
                            "seq": first_seq,
                            "pair": i,
                            "slot": "first",
                            "created_at": time.time(),
                        },
                        session=session,
                    )

                    if args.between_writes_delay_ms > 0:
                        time.sleep(
                            args.between_writes_delay_ms / 1000
                        )

                    writer_collection.insert_one(
                        {
                            "_id": f"client_mw:{second_seq}",
                            "client_id": "client_mw",
                            "seq": second_seq,
                            "pair": i,
                            "slot": "second",
                            "created_at": time.time(),
                            "depends_on": first_seq,
                        },
                        session=session,
                    )

                    if args.after_pair_delay_ms > 0:
                        time.sleep(
                            args.after_pair_delay_ms / 1000
                        )

                    sequences = visible_sequences(
                        read_collection,
                        second_seq
                    )

                    max_visible, missing = (
                        find_prefix_gap(sequences)
                    )

                    is_violation = bool(missing)

                    if is_violation:
                        violations += 1

                except Exception as exc:

                    success = False
                    failed_operations += 1
                    error = str(exc)

                    print(
                        f"[ERROR] 第 {i} 对写入失败: {exc}"
                    )

                latency_ms = (
                    time.time() - start_time
                ) * 1000

                rows.append({
                    "timestamp": time.time(),
                    "iteration": i,
                    "client_id": "client_mw",
                    "operation":
                        "WRITE_PAIR_THEN_READ_SECONDARY",
                    "target": read_host,
                    "phase": phase,
                    "first_seq": first_seq,
                    "second_seq": second_seq,
                    "max_visible_seq": max_visible,
                    "missing_before_max":
                        ";".join(
                            str(v)
                            for v in missing[:20]
                        ),
                    "missing_count": len(missing),
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
                result = subprocess.run(
                    [
                        "docker",
                        "network",
                        "inspect",
                        NETWORK_NAME,
                    ],
                    capture_output=True,
                    text=True,
                )

                if partitioned_node not in result.stdout:
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
    print("MW Partition Experiment 完成")
    print("========================================")

    print(f"总写入对数: {len(rows)}")
    print(f"失败操作: {failed_operations}")

    print(
        f"MW violations: "
        f"{violations}/{len(rows)}"
    )

    print(
        f"MW violation rate: "
        f"{violation_rate:.2f}%"
    )

    filename = (
        "results/raw/"
        f"mw_{args.scenario}"
        f"_w{args.write_concern}"
        f"_r{args.read_concern}"
        f"_causal{str(args.causal_session).lower()}.csv"
    )

    save_csv(rows, filename)

    print(f"日志已保存至: {filename}")


if __name__ == "__main__":
    main()
