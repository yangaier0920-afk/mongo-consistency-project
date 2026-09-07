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
| Member 2 | Monotonic Reads 与 Monotonic Writes 实验 |
| Member 3 | Writes-Follow-Reads 实验、故障 / 网络分区注入 |

---

## 2. 快速开始

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

## 3. 运行 RYW 实验

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

## 4. 项目实验内容

整个项目主要包含以下实验：

| Experiment | Consistency Model | 主要内容 |
|---|---|---|
| Experiment 1 | Read Your Writes | 验证客户端写入后是否能够立即读取自己的最新写入 |
| Experiment 2 | Monotonic Reads | 验证客户端后续读取是否会看到比之前更旧的数据 |
| Experiment 3 | Monotonic Writes | 验证同一客户端的连续写操作是否保持正确顺序 |
| Experiment 4 | Writes Follow Reads | 验证读取之后产生的写操作是否保持正确的因果顺序 |

实验过程中还会通过 **Failure / Partition Injection** 模拟节点故障或网络异常，以观察不同 MongoDB 配置下 Client-Centric Consistency 的表现。

---

## 5. 推荐运行流程

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
