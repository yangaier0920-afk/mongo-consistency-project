import argparse
import time
import os
import csv
import subprocess

from pymongo import MongoClient, ReadPreference, WriteConcern
from pymongo.read_concern import ReadConcern

from common import docker_network_contains, resolve_docker_network

NETWORK_NAME = None


def parse_args():
    parser = argparse.ArgumentParser(
        description="MongoDB RYW Network Partition Experiment"
    )

    parser.add_argument("--write-concern", type=str, default="1")
    parser.add_argument("--read-concern", type=str, default="local")
    parser.add_argument("--scenario", type=str, default="partition")
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument(
        "--network",
        default=None,
        help="Docker network name. If omitted, auto-detect *_mongodb-network.",
    )

    parser.add_argument(
        "--causal-session",
        action="store_true",
        help="Enable causally consistent session"
    )

    return parser.parse_args()


def get_primary_node(client):
    hello = client.admin.command("hello")
    primary = hello.get("primary")

    if not primary:
        return None

    return primary.split(":")[0]


def partition_node(node):
    print(f"\n[PARTITION] 正在隔离 Primary: {node}")

    subprocess.run(
        [
            "docker",
            "network",
            "disconnect",
            "-f",
            NETWORK_NAME,
            node,
        ],
        check=True,
    )

    print(
        f"[PARTITION] {node} 已与 MongoDB 网络断开"
    )


def restore_node_network(node):
    print(
        f"\n[RECOVERY] 正在恢复 {node} 的网络"
    )

    subprocess.run(
        [
            "docker",
            "network",
            "connect",
            NETWORK_NAME,
            node,
        ],
        check=True,
    )

    print(
        f"[RECOVERY] {node} 已重新连接 MongoDB 网络"
    )


def main():
    global NETWORK_NAME
    args = parse_args()
    NETWORK_NAME = resolve_docker_network(args.network)

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

    collection.delete_many({})

    collection.insert_one({
        "id": "item1",
        "value": 500,
        "version": 0,
        "last_writer": "client_A"
    })

    print("========================================")
    print("启动 RYW Network Partition Experiment")
    print("========================================")
    print(f"WriteConcern   = {args.write_concern}")
    print(f"ReadConcern    = {args.read_concern}")
    print(f"Causal Session = {args.causal_session}")
    print(f"Scenario       = {args.scenario}")
    print(f"Iterations     = {args.iterations}")
    print(f"Docker network = {NETWORK_NAME}")
    print("----------------------------------------")

    violations = 0
    successful_operations = 0
    failed_operations = 0

    logs = []
    partitioned_node = None

    try:
        with client.start_session(
            causal_consistency=args.causal_session
        ) as session:

            for i in range(1, args.iterations + 1):

                # 第 200 次：隔离当前 Primary
                if (
                    args.scenario == "partition"
                    and i == 200
                ):
                    print("\n========================================")
                    print("开始注入 Network Partition")
                    print("========================================")

                    try:
                        partitioned_node = get_primary_node(
                            client
                        )

                        print(
                            f"[PARTITION] 当前 Primary = "
                            f"{partitioned_node}"
                        )

                        if partitioned_node:
                            partition_node(
                                partitioned_node
                            )

                            print(
                                "[PARTITION] 等待剩余节点 "
                                "进行 election..."
                            )

                    except Exception as exc:
                        print(
                            f"[PARTITION] 注入失败: {exc}"
                        )

                # 第 600 次：恢复网络
                if (
                    args.scenario == "partition"
                    and i == 600
                ):
                    print("\n========================================")
                    print("开始恢复网络")
                    print("========================================")

                    if partitioned_node:
                        try:
                            restore_node_network(
                                partitioned_node
                            )
                        except Exception as exc:
                            print(
                                f"[RECOVERY] 网络恢复失败: "
                                f"{exc}"
                            )

                if i < 200:
                    phase = "normal"
                elif i < 600:
                    phase = "partition"
                else:
                    phase = "recovery"

                written_version = i

                success = True
                observed_version = -1
                is_violation = False
                error_message = ""

                start_time = time.time()

                try:
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

                    doc = collection.find_one(
                        {"id": "item1"},
                        session=session
                    )

                    if doc:
                        observed_version = doc.get(
                            "version",
                            -1
                        )

                    if observed_version < written_version:
                        violations += 1
                        is_violation = True

                    successful_operations += 1

                except Exception as exc:
                    success = False
                    failed_operations += 1
                    error_message = str(exc)

                    print(
                        f"[ERROR] 第 {i} 次操作失败: {exc}"
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
                    "partitioned_node":
                        partitioned_node or "",
                    "requested_version":
                        written_version,
                    "observed_version":
                        observed_version,
                    "read_concern":
                        args.read_concern,
                    "write_concern":
                        args.write_concern,
                    "causal_session":
                        args.causal_session,
                    "latency_ms":
                        round(latency_ms, 2),
                    "success":
                        success,
                    "violation":
                        is_violation,
                    "error":
                        error_message
                })

                if i % 100 == 0:
                    print(
                        f"[PROGRESS] "
                        f"{i}/{args.iterations} | "
                        f"phase={phase} | "
                        f"violations={violations} | "
                        f"failed={failed_operations}"
                    )
    finally:
        if partitioned_node is not None:
            try:
                if not docker_network_contains(
                    NETWORK_NAME,
                    partitioned_node,
                ):
                    print(
                        f"[RECOVERY] Safety reconnect: {partitioned_node}"
                    )
                    restore_node_network(partitioned_node)
            except Exception as exc:
                print(f"WARNING: Safety recovery failed: {exc}")

    violation_rate = (
        violations / args.iterations
    ) * 100

    success_rate = (
        successful_operations
        / args.iterations
    ) * 100

    print("\n========================================")
    print("RYW Partition Experiment 完成")
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
        "results/raw/"
        f"ryw_{args.scenario}"
        f"_w{args.write_concern}"
        f"_r{args.read_concern}"
        f"_causal{causal_text}.csv"
    )

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

    print(
        f"日志已保存至: {filename}"
    )


if __name__ == "__main__":
    main()
