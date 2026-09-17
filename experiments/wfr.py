import argparse
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
    parse_direct_hosts,
    replica_client,
    reset_collection,
    save_csv,
    secondary_hosts,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="MongoDB Writes-Follow-Reads Experiment"
    )

    parser.add_argument("--write-concern", type=str, default="1")
    parser.add_argument("--read-concern", type=str, default="local")
    parser.add_argument("--scenario", type=str, default="normal")
    parser.add_argument("--iterations", type=int, default=1000)

    parser.add_argument("--uri", type=str, default=DEFAULT_URI)
    parser.add_argument("--direct-hosts", type=str, default=DEFAULT_DIRECT_HOSTS)
    parser.add_argument("--database", type=str, default=DEFAULT_DB)
    parser.add_argument("--collection", type=str, default="wfr_collection")

    parser.add_argument(
        "--causal-session",
        action="store_true",
        help="Run dependent read/write operations inside one causally consistent session.",
    )

    parser.add_argument(
        "--after-v1-delay-ms",
        type=float,
        default=0,
        help="Delay after writing v1 before Client B reads it.",
    )

    parser.add_argument(
        "--after-v2-delay-ms",
        type=float,
        default=0,
        help="Delay after writing v2 before observing a secondary.",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    wc = build_write_concern(args.write_concern)
    rc = build_read_concern(args.read_concern)

    client = replica_client(args.uri)
    client.admin.command("ping")

    members = discover_members(parse_direct_hosts(args.direct_hosts))
    secondaries = secondary_hosts(members)

    if not secondaries:
        raise RuntimeError(
            "Writes-Follow-Reads requires at least one SECONDARY node."
        )

    reset_collection(
        client,
        args.database,
        args.collection,
    )

    # Client A / B write through the replica-set PRIMARY.
    writer_collection = collection_for_client(
        client,
        args.database,
        args.collection,
        write_concern=wc,
        read_preference=ReadPreference.PRIMARY,
    )

    # Client B reads the prerequisite value.
    primary_reader = collection_for_client(
        client,
        args.database,
        args.collection,
        read_concern=rc,
        read_preference=ReadPreference.PRIMARY,
    )

    # Used when causal session is enabled.
    session_secondary_reader = collection_for_client(
        client,
        args.database,
        args.collection,
        read_concern=rc,
        read_preference=ReadPreference.SECONDARY,
    )

    # Directly inspect individual secondary nodes in weak configuration.
    direct_secondary_readers = [
        (
            host,
            *collection_for_direct_host(
                host,
                args.database,
                args.collection,
                rc,
            ),
        )
        for host in secondaries
    ]

    print("=== 启动 Writes-Follow-Reads 实验 ===")
    print(
        f"配置: WriteConcern={args.write_concern}, "
        f"ReadConcern={args.read_concern}"
    )
    print(f"场景: {args.scenario}, 迭代次数: {args.iterations}")
    print(f"Causal Session: {args.causal_session}")
    print(f"Secondary targets: {', '.join(secondaries)}")

    rows = []
    violations = 0

    session_context = (
        client.start_session(causal_consistency=True)
        if args.causal_session
        else nullcontext(None)
    )

    try:
        with session_context as session:

            for i in range(1, args.iterations + 1):

                prerequisite_id = f"v1:{i}"
                dependent_id = f"v2:{i}"

                success = True
                error = ""

                read_v1 = False
                v1_visible_on_observer = False
                v2_visible_on_observer = False
                is_violation = False

                # Alternate between secondary nodes.
                read_host, _, direct_read_collection = (
                    direct_secondary_readers[
                        (i - 1) % len(direct_secondary_readers)
                    ]
                )

                start_time = time.time()

                try:
                    # -------------------------------------------------
                    # Step 1
                    # Client A writes prerequisite event v1.
                    # -------------------------------------------------
                    writer_collection.insert_one(
                        {
                            "_id": prerequisite_id,
                            "iteration": i,
                            "type": "prerequisite",
                            "version": 1,
                            "writer": "client_A",
                            "created_at": time.time(),
                        },
                        session=session,
                    )

                    if args.after_v1_delay_ms > 0:
                        time.sleep(args.after_v1_delay_ms / 1000)

                    # -------------------------------------------------
                    # Step 2
                    # Client B reads v1.
                    # -------------------------------------------------
                    prerequisite = primary_reader.find_one(
                        {"_id": prerequisite_id},
                        session=session,
                    )

                    read_v1 = prerequisite is not None

                    # B must actually observe v1 before producing v2.
                    if not read_v1:
                        raise RuntimeError(
                            f"Client B could not read prerequisite "
                            f"{prerequisite_id}"
                        )

                    # -------------------------------------------------
                    # Step 3
                    # Client B writes v2 after having read v1.
                    #
                    # v2 explicitly records dependency on v1.
                    # -------------------------------------------------
                    writer_collection.insert_one(
                        {
                            "_id": dependent_id,
                            "iteration": i,
                            "type": "dependent",
                            "version": 2,
                            "writer": "client_B",
                            "depends_on": prerequisite_id,
                            "created_at": time.time(),
                        },
                        session=session,
                    )

                    if args.after_v2_delay_ms > 0:
                        time.sleep(args.after_v2_delay_ms / 1000)

                    # -------------------------------------------------
                    # Step 4
                    # Observe a SECONDARY.
                    #
                    # Weak experiment:
                    # directly inspect one particular secondary.
                    #
                    # Causal experiment:
                    # use secondary read preference in causal session.
                    # -------------------------------------------------
                    observer_collection = (
                        session_secondary_reader
                        if args.causal_session
                        else direct_read_collection
                    )

                    observer_target = (
                        "session_secondary"
                        if args.causal_session
                        else read_host
                    )

                    observed_v1 = observer_collection.find_one(
                        {"_id": prerequisite_id},
                        session=session,
                    )

                    observed_v2 = observer_collection.find_one(
                        {"_id": dependent_id},
                        session=session,
                    )

                    v1_visible_on_observer = observed_v1 is not None
                    v2_visible_on_observer = observed_v2 is not None

                    # -------------------------------------------------
                    # WFR violation:
                    #
                    # observer sees the dependent write v2,
                    # but does NOT see the read prerequisite v1.
                    # -------------------------------------------------
                    is_violation = (
                        v2_visible_on_observer
                        and not v1_visible_on_observer
                    )

                    if is_violation:
                        violations += 1

                except Exception as exc:
                    success = False
                    error = str(exc)

                    observer_target = (
                        "session_secondary"
                        if args.causal_session
                        else read_host
                    )

                    print(f"第 {i} 次 WFR 实验失败: {exc}")

                latency_ms = (time.time() - start_time) * 1000

                rows.append(
                    {
                        "timestamp": time.time(),
                        "iteration": i,
                        "client_id": "client_wfr",
                        "operation": "READ_V1_WRITE_V2_READ_SECONDARY",
                        "target": observer_target,
                        "prerequisite_id": prerequisite_id,
                        "dependent_id": dependent_id,
                        "client_b_read_v1": read_v1,
                        "v1_visible_on_observer": v1_visible_on_observer,
                        "v2_visible_on_observer": v2_visible_on_observer,
                        "read_concern": args.read_concern,
                        "write_concern": args.write_concern,
                        "causal_session": args.causal_session,
                        "scenario": args.scenario,
                        "latency_ms": round(latency_ms, 2),
                        "success": success,
                        "violation": is_violation,
                        "error": error,
                    }
                )

    finally:
        for _, direct_reader_client, _ in direct_secondary_readers:
            direct_reader_client.close()

        client.close()

    violation_rate = (
        (violations / len(rows)) * 100
        if rows
        else 0
    )

    print(
        f"\n实验完成! WFR 违背次数: "
        f"{violations}/{len(rows)} "
        f"(违背率: {violation_rate:.2f}%)"
    )

    filename = (
        "results/raw/"
        f"wfr_{args.scenario}"
        f"_w{args.write_concern}"
        f"_r{args.read_concern}"
        f"_causal{str(args.causal_session).lower()}.csv"
    )

    save_csv(rows, filename)
    print(f"日志已保存至: {filename}")


if __name__ == "__main__":
    main()

