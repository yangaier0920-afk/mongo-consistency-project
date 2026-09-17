import argparse
import time
import os
import csv
import subprocess

from pymongo import MongoClient, ReadPreference, WriteConcern
from pymongo.read_concern import ReadConcern


def parse_args():
    parser = argparse.ArgumentParser(
        description="MongoDB RYW Primary Failure Experiment"
    )

    parser.add_argument(
        "--write-concern",
        type=str,
        default="1"
    )

    parser.add_argument(
        "--read-concern",
        type=str,
        default="local"
    )

    parser.add_argument(
        "--scenario",
        type=str,
        default="failure"
    )

    parser.add_argument(
        "--iterations",
        type=int,
        default=1000
    )

    parser.add_argument(
        "--causal-session",
        action="store_true",
        help="Enable MongoDB causal consistency session"
    )

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


def main():
    args = parse_args()

    client = MongoClient(
        "mongodb://localhost:27017,"
        "localhost:27018,"
        "localhost:27019/"
        "?replicaSet=rs0",
        serverSelectionTimeoutMS=15000
    )

    db = client["dsa5208_db"]

    wc_value = (
        int(args.write_concern)
        if args.write_concern.isdigit()
        else args.write_concern
    )

    wc = WriteConcern(w=wc_value)
    rc = ReadConcern(level=args.read_concern)

    collection = db.get_collection(
        "ryw_collection",
        write_concern=wc,
        read_concern=rc,
        read_preference=ReadPreference.SECONDARY
    )

    # 初始化
    collection.delete_many({})

    collection.insert_one({
        "id": "item1",
        "value": 500,
        "version": 0,
        "last_writer": "client_A"
    })

    print("========================================")
    print("启动 RYW Primary Failure Experiment")
    print("========================================")
    print(f"WriteConcern   = {args.write_concern}")
    print(f"ReadConcern    = {args.read_concern}")
    print(f"Causal Session = {args.causal_session}")
    print(f"Scenario       = {args.scenario}")
    print(f"Iterations     = {args.iterations}")
    print("----------------------------------------")

    violations = 0
    successful_operations = 0
    failed_operations = 0

    logs = []

    failed_node = None

    # 同一个 client session 贯穿整个实验
    with client.start_session(
        causal_consistency=args.causal_session
    ) as session:

        for i in range(1, args.iterations + 1):

            # ==========================================
            # 第 200 次：停止当前 Primary
            # ==========================================

            if args.scenario == "failure" and i == 200:

                print("\n========================================")
                print("开始注入 Primary Failure")
                print("========================================")

                try:
                    failed_node = get_primary_node(client)

                    print(
                        f"[FAULT] 当前 Primary = {failed_node}"
                    )

                    if failed_node:
                        stop_node(failed_node)

                        print(
                            "[FAULT] 等待 Replica Set "
                            "进行新的 Primary election..."
                        )

                    else:
                        print(
                            "[FAULT] 没有检测到 Primary"
                        )

                except Exception as e:
                    print(
                        f"[FAULT] 故障注入失败: {e}"
                    )

            # ==========================================
            # 第 600 次：恢复原来的节点
            # ==========================================

            if args.scenario == "failure" and i == 600:

                print("\n========================================")
                print("开始恢复故障节点")
                print("========================================")

                if failed_node:
                    try:
                        start_node(failed_node)

                    except Exception as e:
                        print(
                            f"[RECOVERY] 节点恢复失败: {e}"
                        )

            # ==========================================
            # 当前阶段
            # ==========================================

            if i < 200:
                phase = "normal"

            elif i < 600:
                phase = "failure"

            else:
                phase = "recovery"

            written_version = i

            success = True
            observed_version = -1
            is_violation = False
            error_message = ""

            start_time = time.time()

            try:

                # --------------------------------------
                # WRITE
                # --------------------------------------

                collection.update_one(
                    {"id": "item1"},
                    {
                        "$set": {
                            "version": written_version,
                            "last_writer": "client_A"
                        }
                    },
                    session=session
                )

                # --------------------------------------
                # READ
                #
                # 故意使用 Secondary read preference
                # --------------------------------------

                doc = collection.find_one(
                    {"id": "item1"},
                    session=session
                )

                if doc:
                    observed_version = doc.get(
                        "version",
                        -1
                    )

                # --------------------------------------
                # RYW violation
                #
                # 写入 version=i 后，
                # 如果读到的 version < i，
                # 则没有读到自己的写入。
                # --------------------------------------

                if observed_version < written_version:
                    violations += 1
                    is_violation = True

                successful_operations += 1

            except Exception as e:

                success = False
                failed_operations += 1
                error_message = str(e)

                print(
                    f"[ERROR] 第 {i} 次操作失败: {e}"
                )

            latency_ms = (
                time.time() - start_time
            ) * 1000

            logs.append({
                "iteration": i,
                "timestamp": time.time(),
                "client_id": "client_A",
                "operation": "WRITE_THEN_READ",
                "phase": phase,
                "scenario": args.scenario,
                "failed_node": failed_node or "",
                "requested_version": written_version,
                "observed_version": observed_version,
                "read_concern": args.read_concern,
                "write_concern": args.write_concern,
                "causal_session": args.causal_session,
                "latency_ms": round(latency_ms, 2),
                "success": success,
                "violation": is_violation,
                "error": error_message
            })

            if i % 100 == 0:
                print(
                    f"[PROGRESS] "
                    f"{i}/{args.iterations} | "
                    f"phase={phase} | "
                    f"violations={violations} | "
                    f"failed={failed_operations}"
                )

    violation_rate = (
        violations / args.iterations
    ) * 100

    success_rate = (
        successful_operations / args.iterations
    ) * 100

    print("\n========================================")
    print("RYW Failure Experiment 完成")
    print("========================================")

    print(f"总操作数: {args.iterations}")
    print(f"成功操作: {successful_operations}")
    print(f"失败操作: {failed_operations}")
    print(f"Success Rate: {success_rate:.2f}%")
    print(
        f"RYW violations: "
        f"{violations}/{args.iterations}"
    )
    print(
        f"RYW violation rate: "
        f"{violation_rate:.2f}%"
    )

    os.makedirs(
        "results/raw",
        exist_ok=True
    )

    causal_text = (
        "true"
        if args.causal_session
        else "false"
    )

    filename = (
        f"results/raw/"
        f"ryw_{args.scenario}"
        f"_w{args.write_concern}"
        f"_r{args.read_concern}"
        f"_causal{causal_text}.csv"
    )

    if logs:
        with open(
            filename,
            mode="w",
            newline=""
        ) as file:

            writer = csv.DictWriter(
                file,
                fieldnames=logs[0].keys()
            )

            writer.writeheader()
            writer.writerows(logs)

    print(f"日志已保存至: {filename}")


if __name__ == "__main__":
    main()
