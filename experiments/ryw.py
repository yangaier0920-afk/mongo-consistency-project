import argparse
import time
import os
import csv
from pymongo import MongoClient, ReadPreference, WriteConcern
from pymongo.read_concern import ReadConcern

def parse_args():
    parser = argparse.ArgumentParser(description="MongoDB RYW Consistency Experiment")
    parser.add_argument("--write-concern", type=str, default="1", help="Write concern (e.g., 1 or majority)")
    parser.add_argument("--read-concern", type=str, default="local", help="Read concern (e.g., local or majority)")
    parser.add_argument("--scenario", type=str, default="normal", help="Experiment scenario (normal/failure/partition)")
    parser.add_argument("--iterations", type=int, default=1000, help="Number of read/write operations")
    return parser.parse_args()

def main():
    args = parse_args()
    
    # 1. 连接到整个副本集
    client = MongoClient("mongodb://localhost:27017,localhost:27018,localhost:27019/?replicaSet=rs0")
    db = client["dsa5208_db"]
    
    # 2. 根据命令行参数配置 WriteConcern 和 ReadConcern
    # WriteConcern 需要区分整数 1 和字符串 "majority"
    wc_value = int(args.write_concern) if args.write_concern.isdigit() else args.write_concern
    wc = WriteConcern(w=wc_value)
    rc = ReadConcern(level=args.read_concern)
    
    # 3. 获取 Collection 并应用配置。RYW 实验刻意从 Secondary 读取以最大化 stale read 概率
    collection = db.get_collection(
        "ryw_collection",
        write_concern=wc,
        read_concern=rc,
        read_preference=ReadPreference.SECONDARY
    )
    
    # 初始化基础文档
    collection.delete_many({})
    collection.insert_one({
        "id": "item1",
        "value": 500,
        "version": 0,
        "last_writer": "client_A"
    })
    
    print(f"=== 启动 RYW 实验 ===")
    print(f"配置: WriteConcern={args.write_concern}, ReadConcern={args.read_concern}")
    print(f"场景: {args.scenario}, 迭代次数: {args.iterations}")
    
    violations = 0
    logs = []
    
    # 4. 执行高频自动化读写循环
    for i in range(1, args.iterations + 1):
        written_version = i
        success = True
        observed_version = -1
        is_violation = False
        
        start_time = time.time()
        
        try:
            # 执行写操作 (提升版本号)
            collection.update_one({"id": "item1"}, {"$set": {"version": written_version}})
            
            # 立即执行读操作
            doc = collection.find_one({"id": "item1"})
            if doc:
                observed_version = doc.get("version", -1)
                
            # RYW 违规判定逻辑：如果读到的版本落后于刚刚写入的版本
            if observed_version < written_version:
                violations += 1
                is_violation = True
                
        except Exception as e:
            success = False
            print(f"第 {i} 次操作失败: {e}")
            
        latency_ms = (time.time() - start_time) * 1000
        
        # 按照指南建议的标准格式记录日志
        logs.append({
            "timestamp": time.time(),
            "client_id": "client_A",
            "operation": "WRITE_THEN_READ",
            "requested_version": written_version,
            "observed_version": observed_version,
            "read_concern": args.read_concern,
            "write_concern": args.write_concern,
            "scenario": args.scenario,
            "latency_ms": round(latency_ms, 2),
            "success": success,
            "violation": is_violation
        })
        
    # 5. 打印结果并保存至 CSV
    violation_rate = (violations / args.iterations) * 100
    print(f"\n实验完成! RYW 违背次数: {violations}/{args.iterations} (违背率: {violation_rate:.2f}%)")
    
    os.makedirs("results/raw", exist_ok=True)
    filename = f"results/raw/ryw_{args.scenario}_w{args.write_concern}_r{args.read_concern}.csv"
    
    with open(filename, mode='w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=logs[0].keys())
        writer.writeheader()
        writer.writerows(logs)
        
    print(f"日志已保存至: {filename}")

if __name__ == "__main__":
    main()