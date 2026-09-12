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
