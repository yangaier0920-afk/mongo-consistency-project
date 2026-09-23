import argparse
import time
import subprocess
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
        description="MongoDB Monotonic Reads Failure Experiment"
    )

    parser.add_argument("--write-concern", type=str, default="1")
    parser.add_argument("--read-concern", type=str, default="local")
    parser.add_argument("--scenario", type=str, default="failure")
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


def get_primary_node(client):
    hello = client.admin.command("hello")
    primary = hello.get("primary")

    if not primary:
        return None

    return primary.split(":")[0]


def stop_node(node):
    print(f"\n[FAULT] 正在停止 Primary: {node}")

    subprocess.run(
        ["docker", "stop", node],
        check=True
    )

    print(f"[FAULT] {node} 已停止")


def start_node(node):
    print(f"\n[RECOVERY] 正在恢复节点: {node}")

    subprocess.run(
        ["docker", "start", node],
        check=True
    )

    print(f"[RECOVERY] {node} 已重新启动")


def read_version(collection, session=None):
    doc = collection.find_one(
        {"id": "item1"},
        session=session
    )

    return doc.get("version", -1) if doc else -1


def log_read(
    rows,
    args,
    iteration,
    phase,
    failed_node,
    operation,
    target,
    written_version,
    observed_version,
    max_seen_before,
    latency_ms,
    success,
    violation,
    error_message,
):
    rows.append(
        {
            "iteration": iteration,
            "timestamp": time.time(),
            "client_id": "client_mr",
            "operation": operation,
            "target": target,
            "phase": phase,
            "failed_node": failed_node or "",
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
            "error": error_message,
        }
    )


def main():
    args = parse_args()

    wc = build_write_concern(
        args.write_concern
    )

    rc = build_read_concern(
        args.read_concern
    )

    client = replica_client(
        args.uri
    )

    client.admin.command("ping")

    members = discover_members(
        parse_direct_hosts(
            args.direct_hosts
        )
    )

    secondaries = secondary_hosts(
        members
    )

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
    print("启动 Monotonic Reads Failure Experiment")
    print("========================================")

    print(
        f"WriteConcern   = {args.write_concern}"
    )

    print(
        f"ReadConcern    = {args.read_concern}"
    )

    print(
        f"Causal Session = {args.causal_session}"
    )

    print(
        f"Scenario       = {args.scenario}"
    )

    print(
        f"Iterations     = {args.iterations}"
    )

    print(
        f"Secondary targets = {', '.join(secondaries)}"
    )

    print("----------------------------------------")

    rows = []
    violations = 0
    failed_operations = 0
    max_seen = -1
    failed_node = None

    session_context = (
        client.start_session(
            causal_consistency=True
        )
        if args.causal_session
        else nullcontext(None)
    )

    try:
        with session_context as session:

            for i in range(
                1,
                args.iterations + 1
            ):

                # ======================================
                # 第 200 次：停止当前 Primary
                # ======================================

                if (
                    args.scenario == "failure"
                    and i == 200
                ):

                    print(
                        "\n========================================"
                    )

                    print(
                        "开始注入 Primary Failure"
                    )

                    print(
                        "========================================"
                    )

                    try:
                        failed_node = get_primary_node(
                            client
                        )

                        print(
                            f"[FAULT] 当前 Primary = {failed_node}"
                        )

                        if failed_node:

                            stop_node(
                                failed_node
                            )

                            print(
                                "[FAULT] 等待 Replica Set "
                                "重新选举..."
                            )

                        else:

                            print(
                                "[FAULT] 没有检测到 Primary"
                            )

                    except Exception as exc:

                        print(
                            f"[FAULT] 故障注入失败: {exc}"
                        )

                # ======================================
                # 第 600 次：恢复节点
                # ======================================

                if (
                    args.scenario == "failure"
                    and i == 600
                ):

                    print(
                        "\n========================================"
                    )

                    print(
                        "开始恢复故障节点"
                    )

                    print(
                        "========================================"
                    )

                    if failed_node:

                        try:
                            start_node(
                                failed_node
                            )

                        except Exception as exc:

                            print(
                                f"[RECOVERY] 恢复失败: {exc}"
                            )

                # ======================================
                # 当前阶段
                # ======================================

                if i < 200:
                    phase = "normal"

                elif i < 600:
                    phase = "failure"

                else:
                    phase = "recovery"

                # ======================================
                # Background writer
                # ======================================

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
                        args.after_write_delay_ms
                        / 1000
                    )

                # ======================================
                # 两次连续读取：
                # Primary -> Secondary
                #
                # 如果后一次读取比之前见过的最大版本更旧，
                # 则 MR violation
                # ======================================

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
                                (i - 1)
                                % len(direct_secondary_readers)
                            ][0]
                        ),
                        (
                            session_secondary_reader
                            if args.causal_session
                            else direct_secondary_readers[
                                (i - 1)
                                % len(direct_secondary_readers)
                            ][2]
                        ),
                    ),
                ]

                for (
                    operation,
                    target,
                    collection
                ) in read_targets:

                    start_time = time.time()

                    success = True
                    observed_version = -1
                    is_violation = False
                    error_message = ""

                    max_seen_before = max_seen

                    try:

                        observed_version = read_version(
                            collection,
                            session=session
                        )

                        is_violation = (
                            observed_version
                            < max_seen_before
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
                        error_message = str(exc)

                        print(
                            f"[ERROR] 第 {i} 次 "
                            f"{operation} 失败: {exc}"
                        )

                    latency_ms = (
                        time.time()
                        - start_time
                    ) * 1000

                    log_read(
                        rows,
                        args,
                        i,
                        phase,
                        failed_node,
                        operation,
                        target,
                        i,
                        observed_version,
                        max_seen_before,
                        latency_ms,
                        success,
                        is_violation,
                        error_message,
                    )

                    if (
                        args.between_read_delay_ms
                        > 0
                    ):
                        time.sleep(
                            args.between_read_delay_ms
                            / 1000
                        )

                if i % 100 == 0:

                    print(
                        f"[PROGRESS] "
                        f"{i}/{args.iterations} | "
                        f"phase={phase} | "
                        f"violations={violations} | "
                        f"failed={failed_operations}"
                    )

    finally:

        for (
            _,
            direct_reader_client,
            _
        ) in direct_secondary_readers:

            direct_reader_client.close()

        client.close()

    # ==========================================
    # 结果
    # ==========================================

    violation_rate = (
        violations / len(rows)
    ) * 100 if rows else 0

    print("\n========================================")
    print("MR Failure Experiment 完成")
    print("========================================")

    print(
        f"总读取操作数: {len(rows)}"
    )

    print(
        f"失败读取操作: {failed_operations}"
    )

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

    save_csv(
        rows,
        filename
    )

    print(
        f"日志已保存至: {filename}"
    )


if __name__ == "__main__":
    main()
