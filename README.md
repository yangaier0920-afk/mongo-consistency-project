# MongoDB Client-Centric Consistency Project

本项目为 **DSA5208** 课程项目，主要通过 MongoDB Replica Set 实验研究 **Client-Centric Consistency（以客户端为中心的一致性）**。

项目将围绕以下四种一致性模型进行实验：

- **Read Your Writes (RYW)**：读己之写
- **Monotonic Reads (MR)**：单调读
- **Monotonic Writes (MW)**：单调写
- **Writes Follow Reads (WFR)**：写跟随读

实验环境基于 **Docker + MongoDB Replica Set + Python** 搭建。

---

## 1. 团队成员与任务分工

| 成员 | 主要负责内容 |
|---|---|
| Member 1 | 基础环境搭建、Replica Set 初始化、RYW 实验 |
| Member 2 | Monotonic Reads 与 Monotonic Writes 实验（已完成） |
| Member 3 | Writes-Follow-Reads 实验、故障 / 网络分区注入 |

### 当前进度

| 模块 | 状态 | 相关文件 |
|---|---|---|
| Replica Set 初始化 | 已完成，支持默认 Docker 端口，也支持自定义 URI / members | `setup/init_replica.py` |
| Read Your Writes | 已完成 | `experiments/ryw.py` |
| Monotonic Reads | 已完成 | `experiments/monotonic_reads.py` |
| Monotonic Writes | 已完成 | `experiments/monotonic_writes.py` |
| 公共实验工具 | 已完成，用于复用连接、副本集发现、CSV 写入等逻辑 | `experiments/common.py` |
| Writes Follow Reads | 待 Member 3 完成 | 建议新增 `experiments/writes_follow_reads.py` |
| Failure / Partition Injection | 待 Member 3 完成 | 可在现有 `--scenario` 参数基础上扩展 |

---

## 2. 架构与核心设计 (Architecture & Design)

本项目基于 MongoDB 8.0 搭建了 3 节点的副本集（Replica Set），针对客户端因果一致性（Client-Centric Consistency）展开研究。

### 1. 端口隔离与容器网络映射
为了在单机（Localhost）环境下完美模拟真实分布式集群，并彻底解决多节点对外的端口冲突与通信死锁问题，我们采用了**内部端口错位 + 外部端口映射**的策略：
- **`mongo1`**: 内部运行在 `27017`，映射到宿主机 `27017`（担任初始 Primary 节点）
- **`mongo2`**: 内部运行在 `27018`，映射到宿主机 `27018`（Secondary 节点）
- **`mongo3`**: 内部运行在 `27019`，映射到宿主机 `27019`（Secondary 节点）

搭配本地 `hosts` 文件解析（`127.0.0.1 mongo1 mongo2 mongo3`），使得 Python 驱动能够准确识别并连接到对应的集群成员。

---

## 3. 快速开始

### Step 1：克隆项目

首先将项目克隆到本地：

```bash
git clone <your-github-repo-url>
cd mongo-consistency-project
```

---

### Step 2：创建 Python 虚拟环境

建议每位成员使用独立的 Python 虚拟环境，并通过 `requirements.txt` 安装项目所需依赖。

#### Windows

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

#### Mac / Linux

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

### Step 3：配置本地 Hosts

为了让本机能够正确解析 Docker 中的 MongoDB 节点名称，需要添加：

```text
127.0.0.1 mongo1 mongo2 mongo3
```

#### Windows

以**管理员身份**运行记事本，然后打开：

```text
C:\Windows\System32\drivers\etc\hosts
```

在文件末尾添加：

```text
127.0.0.1 mongo1 mongo2 mongo3
```

保存即可。

#### Mac / Linux

运行：

```bash
sudo nano /etc/hosts
```

添加：

```text
127.0.0.1 mongo1 mongo2 mongo3
```

保存并退出。

---

### Step 4：启动 MongoDB Docker 容器

确保 **Docker Desktop 已经启动**，然后在项目根目录运行：

```bash
docker-compose up -d
```

该命令将启动 MongoDB Replica Set 所需的三个 MongoDB 节点。

可以通过以下命令检查容器状态：

```bash
docker ps
```

确认 `mongo1`、`mongo2` 和 `mongo3` 均正常运行。

---

### Step 5：初始化 MongoDB Replica Set

运行项目提供的初始化脚本：

```bash
python setup/init_replica.py
```

该脚本用于初始化 MongoDB Replica Set，并建立三个 MongoDB 节点之间的复制关系。

⚠️ 注意事项（关于初次运行状态）：
初次运行该脚本时，由于后台集群正处于异步选举阶段，你可能会看到所有节点暂时显示为 SECONDARY 或因等待超时而报错。这是正常现象。只需再次运行一次该脚本，即可成功触发并确认 PRIMARY 节点的诞生。

初始化成功后，即可开始运行一致性实验。

---

## 4. 运行 RYW 实验

RYW（Read Your Writes，读己之写）实验可以通过以下命令运行：

```bash
python experiments/ryw.py --write-concern 1 --read-concern local --scenario normal --iterations 1000
```

参数含义：

| 参数 | 含义 |
|---|---|
| `--write-concern 1` | 写操作得到 1 个 MongoDB 节点确认后即可返回 |
| `--read-concern local` | 读取当前节点本地已经接收到的数据 |
| `--scenario normal` | 在正常运行、无故障情况下进行实验 |
| `--iterations 1000` | 重复执行 1000 次实验 |

通过调整不同参数和实验场景，可以观察 MongoDB 在不同配置下是否满足 **Read Your Writes Consistency**。

---

## 5. 运行 Monotonic Reads / Monotonic Writes 实验

Monotonic Reads（单调读）实验用于验证同一客户端后续读取是否会观察到比之前更旧的版本：

```bash
python experiments/monotonic_reads.py --write-concern 1 --read-concern local --scenario normal --iterations 1000
```

Monotonic Writes（单调写）实验用于验证同一客户端连续写入的先后顺序是否会被副本节点反向观察：

```bash
python experiments/monotonic_writes.py --write-concern 1 --read-concern local --scenario normal --iterations 1000
```

若要对比 MongoDB 的因果一致性配置，可以启用 majority concern 与 causal session：

```bash
python experiments/monotonic_reads.py --write-concern majority --read-concern majority --scenario normal --iterations 1000 --causal-session
python experiments/monotonic_writes.py --write-concern majority --read-concern majority --scenario normal --iterations 1000 --causal-session
```

实验结果会写入 `results/raw/` 目录。

### MR / MW 参数说明

| 参数 | 含义 |
|---|---|
| `--write-concern` | 写确认级别，支持 `1` 或 `majority` |
| `--read-concern` | 读隔离级别，支持 `local` 或 `majority` |
| `--scenario` | 实验场景标签，目前用于结果文件命名，Member 3 可扩展为 `failure` / `partition` |
| `--iterations` | MR 表示写入版本次数，MW 表示连续写入对数 |
| `--causal-session` | 启用 MongoDB causal consistency session，用于和弱一致性配置做对照 |
| `--uri` | 副本集连接 URI，默认连接 `localhost:27017-27019` |
| `--direct-hosts` | 直接探测各节点身份的 host 列表，默认 `localhost:27017,localhost:27018,localhost:27019` |

MR 额外支持：

| 参数 | 含义 |
|---|---|
| `--after-write-delay-ms` | 每次写入后等待多少毫秒再读 |
| `--between-read-delay-ms` | primary read 与 secondary read 之间等待多少毫秒 |

MW 额外支持：

| 参数 | 含义 |
|---|---|
| `--between-writes-delay-ms` | 同一客户端连续两次写入之间等待多少毫秒 |
| `--after-pair-delay-ms` | 每组写入完成后等待多少毫秒再从 secondary 观察 |

---

## 6. Member 2 实验结果

本次已完成 Monotonic Reads 与 Monotonic Writes 的脚本实现，并生成以下结果文件：

本次验证环境说明：当前机器没有可用的 Docker CLI，且 `27017` 已被已有 `mongod` 占用，因此实验运行时临时启动了本地 Replica Set `127.0.0.1:27117-27119`。临时节点已在实验结束后关闭。项目默认运行命令仍使用 Docker 映射的 `localhost:27017-27019`。

| 实验 | 配置 | 数据行数 | 违背次数 | 违背率 | 结果文件 |
|---|---|---:|---:|---:|---|
| MR | `w=1`, `readConcern=local`, no causal session | 2000 | 122 | 6.10% | `results/raw/mr_normal_w1_rlocal_causalfalse.csv` |
| MW | `w=1`, `readConcern=local`, no causal session | 1000 | 0 | 0.00% | `results/raw/mw_normal_w1_rlocal_causalfalse.csv` |
| MR | `w=majority`, `readConcern=majority`, causal session | 2000 | 0 | 0.00% | `results/raw/mr_normal_wmajority_rmajority_causaltrue.csv` |
| MW | `w=majority`, `readConcern=majority`, causal session | 1000 | 0 | 0.00% | `results/raw/mw_normal_wmajority_rmajority_causaltrue.csv` |

结论摘要：

- MR 在弱配置下可能出现读版本倒退，因为客户端先从 primary 看到较新版本，随后切换到复制滞后的 secondary 读取旧版本。
- MR 在 `majority + majority + causal session` 配置下未观察到违背。
- MW 在本次正常场景下未观察到顺序违背。MongoDB 单 primary 的 oplog 顺序保证让同一客户端的连续写入按顺序复制。

验证命令：

```bash
python3 -m py_compile setup/init_replica.py experiments/common.py experiments/monotonic_reads.py experiments/monotonic_writes.py
```

---

## 7. Member 3 交接说明

Member 3 后续主要负责 Writes-Follow-Reads（WFR）以及故障 / 网络分区注入。建议直接复用 `experiments/common.py` 中的公共能力：

- `replica_client()`：连接整个 Replica Set。
- `discover_members()` / `secondary_hosts()`：发现 primary / secondary，避免写死当前 primary。
- `collection_for_client()`：按 write concern、read concern、read preference 获取 collection。
- `collection_for_direct_host()`：直接连接某个 secondary，适合制造 stale read 观察点。
- `reset_collection()`：每次实验开始前清空 collection，并用 majority 写入初始化数据。
- `save_csv()`：统一保存实验日志到 `results/raw/`。

WFR 建议实验思路：

1. Client A 写入或更新版本 `v1`。
2. Client B 从某个节点读取 `v1`，记录 `read_version`。
3. Client B 随后写入依赖该读取结果的新版本 `v2`，例如写入字段 `depends_on: v1`。
4. 从 secondary 观察 `v2` 是否在其依赖版本 `v1` 可见之前出现。
5. 若观察到 `v2` 可见但 `v1` 不可见，则记录 WFR violation。

Failure / Partition Injection 建议：

- 现有 MR / MW 脚本已经保留 `--scenario` 参数，可将 `normal` 扩展为 `failure` 或 `partition` 并写入 CSV。
- 若使用 Docker，可通过 `docker stop mongo2`、`docker start mongo2`、网络规则或 Docker network 操作模拟故障。
- 注入故障前后建议记录 `scenario_event`、`target_node`、`event_time`，方便后续分析。
- 注意不要假设 `mongo1` 永远是 primary，应通过 `discover_members()` 或 `hello` 命令动态判断节点角色。

---

## 8. 项目实验内容

整个项目主要包含以下实验：

| Experiment | Consistency Model | 主要内容 |
|---|---|---|
| Experiment 1 | Read Your Writes | 验证客户端写入后是否能够立即读取自己的最新写入 |
| Experiment 2 | Monotonic Reads | 验证客户端后续读取是否会看到比之前更旧的数据 |
| Experiment 3 | Monotonic Writes | 验证同一客户端的连续写操作是否保持正确顺序 |
| Experiment 4 | Writes Follow Reads | 验证读取之后产生的写操作是否保持正确的因果顺序 |

实验过程中还会通过 **Failure / Partition Injection** 模拟节点故障或网络异常，以观察不同 MongoDB 配置下 Client-Centric Consistency 的表现。

---

## 9. 推荐运行流程

首次运行项目时，按照以下顺序执行即可：

```text
Clone Repository
      ↓
Create Python Virtual Environment
      ↓
Install requirements.txt
      ↓
Configure Hosts
      ↓
Start Docker Desktop
      ↓
docker-compose up -d
      ↓
Initialize Replica Set
      ↓
Run Experiments
```

如果 Docker 容器和 Replica Set 已经初始化完成，之后通常只需要：

```bash
docker-compose up -d
```

然后直接运行对应的实验脚本即可。


## 10. Writes-Follow-Reads（WFR）实验

Writes-Follow-Reads（WFR，写跟随读）用于验证：

> 如果客户端 B 已经读取到了某个版本 `v1`，那么客户端 B 后续产生的写入 `v2` 应当保持对 `v1` 的因果依赖关系。

本项目通过两个客户端模拟 WFR：

```text
Client A writes v1
        ↓
Client B reads v1
        ↓
Client B writes v2
        ↓
v2 explicitly depends on v1
        ↓
Observe v1 and v2 from a Secondary
```

其中：

```text
v1 = prerequisite write
v2 = dependent write
```

`v2` 会显式记录：

```text
depends_on = v1
```

---

## 10.1 WFR Violation 判定

如果 Secondary 上出现：

```text
v2 visible = True
v1 visible = False
```

表示：

> Secondary 已经观察到了依赖写 `v2`，但还没有观察到它所依赖的 `v1`。

此时记录：

```text
WFR violation
```

---

## 10.2 WFR 正常场景弱配置

配置：

```text
WriteConcern   = 1
ReadConcern    = local
Causal Session = False
Scenario       = normal
Iterations     = 1000
```

运行：

```bash
python experiments/wfr.py \
  --write-concern 1 \
  --read-concern local \
  --scenario normal \
  --iterations 1000
```

实验结果：

| 指标 | 结果 |
| --- | ---: |
| Iterations | 1000 |
| Successful Operations | 1000 |
| Failed Operations | 0 |
| WFR Violations | 52 |
| Violation Rate | 5.20% |

结果文件：

```text
results/raw/wfr_normal_w1_rlocal_causalfalse.csv
```

弱配置下观察到了 WFR violation。

---

## 10.3 WFR 正常场景强配置

配置：

```text
WriteConcern   = majority
ReadConcern    = majority
Causal Session = True
Scenario       = normal
Iterations     = 1000
```

运行：

```bash
python experiments/wfr.py \
  --write-concern majority \
  --read-concern majority \
  --scenario normal \
  --iterations 1000 \
  --causal-session
```

实验结果：

| 指标 | 结果 |
| --- | ---: |
| Iterations | 1000 |
| Successful Operations | 1000 |
| Failed Operations | 0 |
| WFR Violations | 0 |
| Violation Rate | 0.00% |

结果文件：

```text
results/raw/wfr_normal_wmajority_rmajority_causaltrue.csv
```

本次强配置实验中没有观察到 WFR violation。

---

# 11. Primary Failure 实验

为了研究节点故障对 Client-Centric Consistency 的影响，本项目设计了自动 Primary Failure Injection。

实验脚本不会假设：

```text
mongo1 = PRIMARY
```

而是在故障注入前动态检测当前 PRIMARY。

---

## 11.1 Failure 流程

所有 Failure 实验采用相同的时间线：

```text
Iteration 1-199
      ↓
正常运行

Iteration 200
      ↓
自动检测当前 PRIMARY
      ↓
docker stop PRIMARY
      ↓
Replica Set election

Iteration 200-599
      ↓
Primary Failure 状态

Iteration 600
      ↓
docker start 原故障节点

Iteration 600-1000
      ↓
Recovery 状态
```

注意：

被恢复的节点不一定重新成为 PRIMARY。

例如：

```text
Before failure:
mongo1 PRIMARY

After failure:
mongo2 PRIMARY

mongo1 restart

After recovery:
mongo2 PRIMARY
mongo1 SECONDARY
```

---

## 11.2 Primary Failure 结果

| Consistency Model | Weak Violations | Weak Rate | Strong Violations | Strong Rate |
| --- | ---: | ---: | ---: | ---: |
| RYW | 817 / 1000 | 81.70% | 0 / 1000 | 0.00% |
| MR | 292 / 2000 | 14.60% | 0 / 2000 | 0.00% |
| MW | 0 / 1000 | 0.00% | 0 / 1000 | 0.00% |
| WFR | 22 / 1000 | 2.20% | 0 / 1000 | 0.00% |

---



# 12. Network Partition 实验

Network Partition 与 Primary Failure 不同。

Primary Failure：

```text
docker stop mongoX
```

MongoDB 进程停止。

Network Partition：

```text
docker network disconnect ...
```

MongoDB 容器仍然运行，但是不能和其他 Replica Set 节点通信。

---

## 12.1 Network Partition 流程

统一流程为：

```text
Iteration 1-199
      ↓
正常运行

Iteration 200
      ↓
动态检测当前 PRIMARY
      ↓
Disconnect PRIMARY from Docker network

Iteration 200-599
      ↓
Network Partition

Iteration 600
      ↓
Reconnect original node

Iteration 600-1000
      ↓
Recovery
```

实际使用：

```bash
docker network disconnect -f \
mongo-consistency-project_mongodb-network \
<primary-container>
```

恢复：

```bash
docker network connect \
mongo-consistency-project_mongodb-network \
<partitioned-container>
```

---

## 12.2 Network Partition 结果

| Consistency Model | Weak Violations | Weak Rate | Strong Violations | Strong Rate |
| --- | ---: | ---: | ---: | ---: |
| RYW | 965 / 1000 | 96.50% | 0 / 1000 | 0.00% |
| MR | 330 / 2000 | 16.50% | 0 / 2000 | 0.00% |
| MW | 0 / 1000 | 0.00% | 0 / 1000 | 0.00% |
| WFR | 18 / 1000 | 1.80% | 0 / 1000 | 0.00% |

---


# 13. 四种 Client-Centric Consistency 的结果比较

## 13.1 RYW

弱配置下：

```text
Failure:
81.70%

Partition:
96.50%
```

RYW 对 replication lag 非常敏感。

实验中：

```text
WRITE to Primary
       ↓
immediate READ from Secondary
```

Secondary 如果还没有复制最新写入，就会发生：

```text
observed_version < written_version
```

因此 RYW 的 violation rate 最高。

---

## 13.2 MR

弱配置：

```text
Failure:
14.60%

Partition:
16.50%
```

客户端从 Primary 读取新版本后，再从复制进度较慢的 Secondary 读取时，可能发生版本倒退。

例如：

```text
READ Primary
version = 100

READ Secondary
version = 98
```

则：

```text
98 < 100
```

属于 MR violation。

---

## 13.3 MW

所有实验均为：

```text
0.00%
```

这说明在当前 workload 中没有观察到：

```text
较新的 write 可见
但前面的 write 缺失
```

MongoDB 的单 PRIMARY 写入模式以及 replication ordering 可能使 Secondary 更常表现为一个较旧但有序的 prefix。

因此可能观察：

```text
1 2 3 4 5
```

而 PRIMARY 已经：

```text
1 2 3 4 5 6 7 8
```

这属于 stale replica，但不属于 Monotonic Writes violation。

---

## 13.4 WFR

WFR 弱配置的 violation rate 明显低于 RYW 和 MR：

```text
Normal:
5.20%

Primary Failure:
2.20%

Network Partition:
1.80%
```

原因是 WFR violation 的条件更严格。

必须同时满足：

```text
v2 visible = True
v1 visible = False
```

才被视为 violation。

因此普通的 stale read 并不一定构成 WFR violation。

---

# 14. Strong Configuration 总体结果

强配置：

```text
WriteConcern   = majority
ReadConcern    = majority
Causal Session = True
```

在本次实验中：

| Model | Normal | Failure | Partition |
| --- | ---: | ---: | ---: |
| RYW | 0.10% | 0.00% | 0.00% |
| MR | 0.00% | 0.00% | 0.00% |
| MW | 0.00% | 0.00% | 0.00% |
| WFR | 0.00% | 0.00% | 0.00% |

因此：

> 在本项目的实验环境和 workload 下，`majority` read/write concern 配合 causal session 能显著改善应用观察到的 Client-Centric Consistency。

需要注意：

```text
0 violation
```

表示：

> 本次有限实验中没有观察到 violation。

并不表示对所有可能执行情况进行了形式化证明。

---

# 15. Replica Set Failover 观察

Primary Failure 和 Network Partition 都成功触发了 Replica Set 的 PRIMARY 切换。

例如：

```text
Before fault:
mongo1 PRIMARY

Fault injected:
mongo1 unavailable

After election:
mongo2 PRIMARY
```

另外一次可能为：

```text
Before:
mongo2 PRIMARY

After fault:
mongo3 PRIMARY
```

说明实验脚本不能假设：

```text
mongo1 always PRIMARY
```

因此所有故障脚本均动态检测当前 PRIMARY。

---

# 16. Availability 与 Failover

本项目使用 3 节点 Replica Set。

当一个 PRIMARY 故障或被隔离时：

```text
3 nodes
↓
1 node unavailable
↓
2 nodes remain
↓
2 / 3 = majority
```

剩余两个节点仍然能够形成多数派并进行 PRIMARY election。

因此：

```text
Old PRIMARY unavailable
        ↓
Election
        ↓
New PRIMARY
        ↓
PyMongo topology update
        ↓
Application continues
```

在本次实验记录中，大多数业务操作均能在 scripted failover 后继续完成。

---

# 17. 实验结果总表

## Normal Scenario

| Model | Weak | Strong |
| RYW | 1.8% | 0.10%: |
| MR | 6.10% | 0.00% |
| MW | 0.00% | 0.00% |
| WFR | 5.20% | 0.00% |

RYW 正常场景结果保存在：

```text
results/raw/ryw_normal_w1_rlocal.csv
results/raw/ryw_normal_wmajority_rmajority.csv
```

---

## Primary Failure

| Model | Weak | Strong |
| --- | ---: | ---: |
| RYW | 81.70% | 0.00% |
| MR | 14.60% | 0.00% |
| MW | 0.00% | 0.00% |
| WFR | 2.20% | 0.00% |

---

## Network Partition

| Model | Weak | Strong |
| --- | ---: | ---: |
| RYW | 96.50% | 0.00% |
| MR | 16.50% | 0.00% |
| MW | 0.00% | 0.00% |
| WFR | 1.80% | 0.00% |

---

# 18. 综合结果

本项目最明显的实验结果是：

> **Consistency configuration 对 Client-Centric Consistency 的影响比故障类型本身更加明显和稳定。**

弱配置：

```text
w = 1
readConcern = local
causal consistency = disabled
```

实验表现：

| Model | 观察结果 |
| --- | --- |
| RYW | 大量 violation |
| MR | 存在明显 violation |
| MW | 未观察到 violation |
| WFR | 少量 violation |

强配置：

```text
w = majority
readConcern = majority
causal consistency = enabled
```

实验表现：

```text
RYW → no observed violations
MR  → no observed violations
MW  → no observed violations
WFR → no observed violations
```

Primary Failure 和 Network Partition 会触发 election 和 topology change，但并没有产生一个统一的 violation-rate 增减规律。

---
