from pymongo import MongoClient, ReadPreference

print("=== 开始测试 MongoDB 复制功能 ===")

# 1. 强制直接连接到 PRIMARY (映射在宿主机的 27017 端口)
client_primary = MongoClient("mongodb://localhost:27017/", directConnection=True)
db_primary = client_primary["dsa5208_db"]
collection_primary = db_primary["test_collection"]

print("\n1. 正在向 PRIMARY 写入一条数据...")
collection_primary.insert_one({"test_id": 1, "message": "Hello from Primary!"})
print("   -> 写入成功！")

# 2. 强制直接连接到 SECONDARY (映射在宿主机的 27018 端口)
client_secondary = MongoClient("mongodb://localhost:27018/", directConnection=True)
db_secondary = client_secondary["dsa5208_db"]

# ⚠️注意：MongoDB 默认不允许直接从从节点读取数据。
# 这里运用了项目指南中提到的 Read Preference 概念，显式指定从 Secondary 读取
collection_secondary = db_secondary.get_collection(
    "test_collection", 
    read_preference=ReadPreference.SECONDARY
)

print("\n2. 正在从 SECONDARY 读取刚刚写入的数据...")
result = collection_secondary.find_one({"test_id": 1})
print(f"   -> 从 Secondary 读到的数据: {result}")

if result and result.get("message") == "Hello from Primary!":
    print("\n🎉 测试完美通过！数据已经瞬间从 mongo1 复制到了 mongo2！")
else:
    print("\n❌ 测试失败，未能在 Secondary 找到数据。")