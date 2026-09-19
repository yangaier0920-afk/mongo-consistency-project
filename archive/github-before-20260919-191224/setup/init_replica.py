import argparse
from pymongo import MongoClient
import time
import sys


def parse_args():
    parser = argparse.ArgumentParser(description="Initialize MongoDB replica set")
    parser.add_argument(
        "--uri",
        default="mongodb://localhost:27017/",
        help="Direct connection URI for the node that receives replSetInitiate.",
    )
    parser.add_argument(
        "--members",
        default="mongo1:27017,mongo2:27018,mongo3:27019",
        help="Comma-separated replica set member host:port entries.",
    )
    parser.add_argument("--replica-set", default="rs0")
    parser.add_argument("--wait-seconds", type=int, default=10)
    return parser.parse_args()


def init_replica_set():
    args = parse_args()
    try:
        # 添加了 directConnection=True，强制直接连接单一节点进行初始化
        client = MongoClient(
            args.uri,
            serverSelectionTimeoutMS=5000,
            directConnection=True,
        )
        client.admin.command('ping')
        print(f"成功连接到 {args.uri}, 准备初始化...")
    except Exception as e:
        print(f"连接失败: {e}")
        sys.exit(1)

    config = {
        "_id": args.replica_set,
        "members": [
            {"_id": index, "host": host}
            for index, host in enumerate(
                member.strip() for member in args.members.split(",") if member.strip()
            )
        ]
    }

    try:
        client.admin.command("replSetInitiate", config)
        print(f"复制集初始化命令发送成功！等待{args.wait_seconds}秒选举...")
    except Exception as e:
        print(f"初始化可能已完成或出现错误: {e}")

    time.sleep(args.wait_seconds)
    
    try:
        status = client.admin.command("replSetGetStatus")
        print("\n--- 复制集当前状态 ---")
        for member in status.get("members", []):
            print(f"节点: {member.get('name')} -> 状态: {member.get('stateStr')}")
        print("----------------------")
    except Exception as e:
        print(f"获取状态失败: {e}")

if __name__ == "__main__":
    init_replica_set()
