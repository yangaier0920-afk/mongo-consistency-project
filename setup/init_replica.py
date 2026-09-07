from pymongo import MongoClient
import time
import sys

def init_replica_set():
    try:
        # 添加了 directConnection=True，强制直接连接单一节点进行初始化
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=5000, directConnection=True)
        client.admin.command('ping')
        print("成功连接到 mongo1, 准备初始化...")
    except Exception as e:
        print(f"连接失败: {e}")
        sys.exit(1)

    config = {
        "_id": "rs0",
        "members": [
            {"_id": 0, "host": "mongo1:27017"},
            {"_id": 1, "host": "mongo2:27018"},
            {"_id": 2, "host": "mongo3:27019"}
        ]
    }

    try:
        client.admin.command("replSetInitiate", config)
        print("复制集初始化命令发送成功！等待10秒选举...")
    except Exception as e:
        print(f"初始化可能已完成或出现错误: {e}")

    time.sleep(10)
    
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