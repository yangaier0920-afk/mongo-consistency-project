import argparse
import time
import subprocess
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


def parse_args():
    parser = argparse.ArgumentParser(
        description="MongoDB Monotonic Writes Failure Experiment"
    )

    parser.add_argument("--write-concern", type=str, default="1")
    parser.add_argument("--read-concern", type=str, default="local")
    parser.add_argument("--scenario", type=str, default="failure")
    parser.add_argument("--iterations", type=int, default=1000)

    parser.add_argument("--uri", type=str, default=DEFAULT_URI)
    parser.add_argument("--direct-hosts", type=str, default=DEFAULT_DIRECT_HOSTS)
    parser.add_argument("--database", type=str, default=DEFAULT_DB)
    parser.add_argument("--collection", type=str, default="mw_collection")

    parser.add_argument(
        "--causal-session",
        action="store_true",
        help="Run writes inside one causally consistent session.",
    )

    parser.add_argument("--between-writes-delay-ms", type=float, default=0)
    parser.add_argument("--after-pair-delay-ms", type=float, default=0)

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
    print("启动 Monotonic Writes Failure Experiment")
    print("========================================")

    print(f"WriteConcern   = {args.write_concern}")
    print(f"ReadConcern    = {args.read_concern}")
    print(f"Causal Session = {args.causal_session}")
    print(f"Scenario       = {args.scenario}")
    print(f"Write pairs    = {args.iterations}")

    print(
        f"Secondary targets = "
        f"{', '.join(secondaries)}"
    )

    print("----------------------------------------")

    rows = []
    violations = 0
    failed_operations = 0
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

                # =====================================
                # 第 200 次：停止当前 Primary
                # =====================================

                if (
                    args.scenario == "failure"
                    and i == 200
                ):

                    print(
                        "\n========================================"
                    )
                    print("开始注入 Primary Failure")
                    print(
                        "========================================"
                    )

                    try:
                        failed_node = get_primary_node(
                            client
                        )

                        print(
                            f"[FAULT] 当前 Primary = "
                            f"{failed_node}"
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
                            f"[FAULT] 故障注入失败: "
                            f"{exc}"
                        )

                # =====================================
                # 第 600 次：恢复原节点
                # =====================================

                if (
                    args.scenario == "failure"
                    and i == 600
                ):

                    print(
                        "\n========================================"
                    )
                    print("开始恢复故障节点")
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
                                f"[RECOVERY] 恢复失败: "
                                f"{exc}"
                            )

                # =====================================
                # 当前实验阶段
                # =====================================

                if i < 200:
                    phase = "normal"

                elif i < 600:
                    phase = "failure"

                else:
                    phase = "recovery"

                # 每轮产生两个顺序写入
                first_seq = (i * 2) - 1
                second_seq = i * 2

                read_host, _, read_collection = (
                    direct_secondary_readers[
                        (i - 1)
                        % len(direct_secondary_readers)
                    ]
                )

                success = True
                error = ""
                max_visible = -1
                missing = []
                is_violation = False

                start_time = time.time()

                try:

                    # =================================
                    # 第一次 Write
                    # =================================

                    writer_collection.insert_one(
                        {
                            "_id":
                                f"client_mw:{first_seq}",

                            "client_id":
                                "client_mw",

                            "seq":
                                first_seq,

                            "pair":
                                i,

                            "slot":
                                "first",

                            "created_at":
                                time.time(),
                        },
                        session=session,
                    )

                    if (
                        args.between_writes_delay_ms
                        > 0
                    ):
                        time.sleep(
                            args.between_writes_delay_ms
                            / 1000
                        )

                    # =================================
                    # 第二次 Write
                    #
                    # second_seq 必须发生在 first_seq 后
                    # =================================

                    writer_collection.insert_one(
                        {
                            "_id":
                                f"client_mw:{second_seq}",

                            "client_id":
                                "client_mw",

                            "seq":
                                second_seq,

                            "pair":
                                i,

                            "slot":
                                "second",

                            "created_at":
                                time.time(),

                            "depends_on":
                                first_seq,
                        },
                        session=session,
                    )

                    if (
                        args.after_pair_delay_ms
                        > 0
                    ):
                        time.sleep(
                            args.after_pair_delay_ms
                            / 1000
                        )

                    # =================================
                    # 从 Secondary 检查当前可见写入
                    # =================================

                    sequences = visible_sequences(
                        read_collection,
                        second_seq
                    )

                    max_visible, missing = (
                        find_prefix_gap(
                            sequences
                        )
                    )

                    # 如果已经看到了较新的 seq，
                    # 但前面有某些 seq 缺失，
                    # 则出现 Monotonic Writes violation
                    is_violation = bool(
                        missing
                    )

                    if is_violation:
                        violations += 1

                except Exception as exc:

                    success = False
                    failed_operations += 1
                    error = str(exc)

                    print(
                        f"[ERROR] 第 {i} 对写入失败: "
                        f"{exc}"
                    )

                latency_ms = (
                    time.time()
                    - start_time
                ) * 1000

                rows.append(
                    {
                        "iteration":
                            i,

                        "timestamp":
                            time.time(),

                        "client_id":
                            "client_mw",

                        "operation":
                            "WRITE_PAIR_THEN_READ_SECONDARY",

                        "target":
                            read_host,

                        "phase":
                            phase,

                        "failed_node":
                            failed_node or "",

                        "first_seq":
                            first_seq,

                        "second_seq":
                            second_seq,

                        "max_visible_seq":
                            max_visible,

                        "missing_before_max":
                            ";".join(
                                str(value)
                                for value
                                in missing[:20]
                            ),

                        "missing_count":
                            len(missing),

                        "read_concern":
                            args.read_concern,

                        "write_concern":
                            args.write_concern,

                        "causal_session":
                            args.causal_session,

                        "scenario":
                            args.scenario,

                        "latency_ms":
                            round(
                                latency_ms,
                                2
                            ),

                        "success":
                            success,

                        "violation":
                            is_violation,

                        "error":
                            error,
                    }
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

    # =========================================
    # 最终结果
    # =========================================

    violation_rate = (
        violations / len(rows)
    ) * 100 if rows else 0

    print("\n========================================")
    print("MW Failure Experiment 完成")
    print("========================================")

    print(
        f"总写入对数: {len(rows)}"
    )

    print(
        f"失败操作: {failed_operations}"
    )

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
        f"_causal"
        f"{str(args.causal_session).lower()}"
        f".csv"
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
