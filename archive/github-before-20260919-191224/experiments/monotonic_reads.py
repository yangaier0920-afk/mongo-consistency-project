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
    parser = argparse.ArgumentParser(description="MongoDB Monotonic Reads Experiment")
    parser.add_argument("--write-concern", type=str, default="1")
    parser.add_argument("--read-concern", type=str, default="local")
    parser.add_argument("--scenario", type=str, default="normal")
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--uri", type=str, default=DEFAULT_URI)
    parser.add_argument("--direct-hosts", type=str, default=DEFAULT_DIRECT_HOSTS)
    parser.add_argument("--database", type=str, default=DEFAULT_DB)
    parser.add_argument("--collection", type=str, default="mr_collection")
    parser.add_argument(
        "--causal-session",
        action="store_true",
        help="Run reads inside one causally consistent session.",
    )
    parser.add_argument("--after-write-delay-ms", type=float, default=0)
    parser.add_argument("--between-read-delay-ms", type=float, default=0)
    return parser.parse_args()


def read_version(collection, session=None):
    doc = collection.find_one({"id": "item1"}, session=session)
    return doc.get("version", -1) if doc else -1


def log_read(
    rows,
    args,
    operation,
    target,
    written_version,
    observed_version,
    max_seen_before,
    latency_ms,
    success,
    violation,
):
    rows.append(
        {
            "timestamp": time.time(),
            "client_id": "client_mr",
            "operation": operation,
            "target": target,
            "written_version": written_version,
            "observed_version": observed_version,
            "max_seen_before": max_seen_before,
            "read_concern": args.read_concern,
            "write_concern": args.write_concern,
            "causal_session": args.causal_session,
            "scenario": args.scenario,
            "latency_ms": round(latency_ms, 2),
            "success": success,
            "violation": violation,
        }
    )


def main():
    args = parse_args()
    wc = build_write_concern(args.write_concern)
    rc = build_read_concern(args.read_concern)

    client = replica_client(args.uri)
    client.admin.command("ping")
    members = discover_members(parse_direct_hosts(args.direct_hosts))
    secondaries = secondary_hosts(members)
    if not secondaries:
        raise RuntimeError("Monotonic Reads requires at least one SECONDARY node.")

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
        (host, *collection_for_direct_host(host, args.database, args.collection, rc))
        for host in secondaries
    ]

    print("=== 启动 Monotonic Reads 实验 ===")
    print(f"配置: WriteConcern={args.write_concern}, ReadConcern={args.read_concern}")
    print(f"场景: {args.scenario}, 迭代次数: {args.iterations}")
    print(f"Causal Session: {args.causal_session}")
    print(f"Secondary targets: {', '.join(secondaries)}")

    rows = []
    violations = 0
    max_seen = -1

    session_context = (
        client.start_session(causal_consistency=True)
        if args.causal_session
        else nullcontext(None)
    )

    try:
        with session_context as session:
            for i in range(1, args.iterations + 1):
                writer_collection.update_one(
                    {"id": "item1"},
                    {
                        "$set": {
                            "version": i,
                            "last_writer": "background_writer",
                        }
                    },
                )

                if args.after_write_delay_ms > 0:
                    time.sleep(args.after_write_delay_ms / 1000)

                for operation, target, collection in [
                    ("READ_PRIMARY", "primary", primary_reader),
                    (
                        "READ_SECONDARY",
                        (
                            "session_secondary"
                            if args.causal_session
                            else direct_secondary_readers[
                                (i - 1) % len(direct_secondary_readers)
                            ][0]
                        ),
                        (
                            session_secondary_reader
                            if args.causal_session
                            else direct_secondary_readers[
                                (i - 1) % len(direct_secondary_readers)
                            ][2]
                        ),
                    ),
                ]:
                    start_time = time.time()
                    success = True
                    observed_version = -1
                    is_violation = False
                    max_seen_before = max_seen

                    try:
                        observed_version = read_version(collection, session=session)
                        is_violation = observed_version < max_seen_before
                        if is_violation:
                            violations += 1
                        max_seen = max(max_seen, observed_version)
                    except Exception as exc:
                        success = False
                        print(f"第 {i} 次 {operation} 失败: {exc}")

                    latency_ms = (time.time() - start_time) * 1000
                    log_read(
                        rows,
                        args,
                        operation,
                        target,
                        i,
                        observed_version,
                        max_seen_before,
                        latency_ms,
                        success,
                        is_violation,
                    )

                    if args.between_read_delay_ms > 0:
                        time.sleep(args.between_read_delay_ms / 1000)
    finally:
        for _, direct_reader_client, _ in direct_secondary_readers:
            direct_reader_client.close()
        client.close()

    violation_rate = (violations / len(rows)) * 100
    print(
        f"\n实验完成! MR 违背次数: {violations}/{len(rows)} "
        f"(违背率: {violation_rate:.2f}%)"
    )

    filename = (
        "results/raw/"
        f"mr_{args.scenario}_w{args.write_concern}_r{args.read_concern}"
        f"_causal{str(args.causal_session).lower()}.csv"
    )
    save_csv(rows, filename)
    print(f"日志已保存至: {filename}")


if __name__ == "__main__":
    main()
