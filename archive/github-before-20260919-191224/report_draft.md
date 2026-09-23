# MongoDB Client-Centric Consistency 实验报告（初步修正版）

> 提交前待补充：小组成员姓名、学号、最终统一重跑后的 CSV 结果、PDF 排版。

## 摘要

本项目基于 MongoDB Replica Set 搭建三节点复制式分布式数据库，研究不同一致性配置和故障场景对 client-centric consistency 的影响。实验覆盖四种模型：Read-Your-Writes (RYW)、Monotonic Reads (MR)、Monotonic Writes (MW) 和 Writes-Follow-Reads (WFR)。主要比较两类配置：弱配置 `writeConcern=1, readConcern=local` 与强配置 `writeConcern=majority, readConcern=majority, causal session=true`。

初步实验表明，弱配置下 RYW 和 MR 容易出现违背，尤其在 primary failure 和 network partition 场景中更明显；MW 在所有已有实验中均未观察到违背，这与 MongoDB 单 Primary 和 oplog 顺序复制机制一致；WFR 的旧结果显示弱配置可能出现违背，但旧观察顺序存在误报风险，因此脚本已修正为先观察 dependent write `v2`，再检查 prerequisite write `v1`。强配置在已有结果中基本消除了四种模型的违背。整体上，实验支持这样的结论：MongoDB 弱配置提供更低延迟和更高可用性，但可能牺牲客户端中心一致性；majority concern 与 causal session 能提供更强的一致性保障。

## 1. 项目目标

课程要求我们选择一个支持 tunable consistency 的分布式数据库，搭建复制式部署，探索不同配置对应用可观察一致性的影响，并在 normal operation、node failure 和 network partition 等场景下开展实验。

本项目选择 MongoDB。实验关注以下四种 client-centric consistency：

| 模型 | 含义 |
|---|---|
| Read-Your-Writes (RYW) | 客户端完成写入后，后续读取应能看到自己的写入 |
| Monotonic Reads (MR) | 同一客户端后续读取不应看到比之前更旧的版本 |
| Monotonic Writes (MW) | 同一客户端发出的连续写入应保持顺序 |
| Writes-Follow-Reads (WFR) | 客户端读到某版本后产生的新写入，应保留对该版本的因果依赖 |

## 2. 数据库与部署架构

### 2.1 MongoDB Replica Set

MongoDB Replica Set 由一个 Primary 和若干 Secondary 组成。所有写入首先进入 Primary，再通过 oplog 异步复制到 Secondary。Primary 不可用时，Replica Set 通过 election 选出新的 Primary。该架构天然提供复制容错，同时也允许客户端通过 read concern、write concern、read preference 和 session 配置在一致性、延迟和可用性之间权衡。

### 2.2 三节点部署

目标部署为 Docker 上的三节点 Replica Set：

| 容器 | MongoDB 角色 | 内部端口 | 主机端口 |
|---|---|---:|---:|
| `mongo1` | Primary 或 Secondary | 27017 | 27017 |
| `mongo2` | Primary 或 Secondary | 27018 | 27018 |
| `mongo3` | Primary 或 Secondary | 27019 | 27019 |

脚本不会假设 `mongo1` 永远是 Primary。Failure 和 Partition 实验会运行时检测当前 Primary，再执行故障注入。

### 2.3 软件版本

| 组件 | 版本 / 说明 |
|---|---|
| MongoDB Docker image | `mongo:8.0`，由 `docker-compose.yml` 指定 |
| Python | `3.12.1` |
| PyMongo | 当前环境 `4.17.0`，`requirements.txt` 指定 `4.18.0` |
| 本机补充验证 | 临时 Replica Set `127.0.0.1:27117-27119`，`mongod v7.0.26` |

注：由于当前辅助运行环境没有 Docker CLI，normal 场景曾使用本机临时 Replica Set 补充重跑；failure 和 partition 的现场统一重跑仍建议在完整 Docker 环境中执行。

## 3. 一致性配置与预测

本项目主要比较以下两类配置：

| 配置 | Write Concern | Read Concern | Read Preference | Causal Session | 预期 |
|---|---|---|---|---|---|
| 弱配置 | `1` | `local` | 主要读 Secondary | False | 容易读到 stale data，RYW/MR/WFR 可能违背 |
| 强配置 | `majority` | `majority` | 视实验选择 Primary 或 Secondary | True | 应显著减少或消除四类违背 |

理论预测如下：

| 模型 | 弱配置预测 | 强配置预测 |
|---|---|---|
| RYW | 可能违背。写入由 Primary 确认后立即读 Secondary，Secondary 可能尚未复制到新版本 | 应满足 |
| MR | 可能违背。客户端先读到较新版本，再切到落后的 Secondary，可能出现版本倒退 | 应满足 |
| MW | 大概率满足。MongoDB 单 Primary 通过 oplog 顺序复制同一客户端写入 | 应满足 |
| WFR | 可能违背。弱配置不保证完整因果依赖传播 | 应满足 |

在 primary failure 和 network partition 场景下，弱配置更可能暴露 stale read、角色切换和复制滞后带来的异常；强配置则可能通过 majority concern 和 causal session 提供更强保证，但代价是更高延迟或在多数派不可用时出现等待/失败。

## 4. 实验设计

### 4.1 通用设计

所有实验日志写入 `results/raw/*.csv`。日志字段包括：iteration、timestamp、client_id、operation、scenario、phase、read_concern、write_concern、causal_session、latency_ms、success、violation，以及具体模型相关字段。

实验覆盖三类场景：

| 场景 | 设计 |
|---|---|
| Normal | 三节点 Replica Set 正常运行 |
| Primary Failure | 第 200 次迭代停止当前 Primary，第 600 次迭代恢复该节点 |
| Network Partition | 第 200 次迭代将当前 Primary 从 Docker network 中断开，第 600 次迭代重新连接 |

Network partition 脚本已支持 `--network` 参数，并可自动发现 `*_mongodb-network`，避免 Compose 项目名变化导致网络名不匹配。

### 4.2 RYW

脚本：`experiments/ryw.py`、`experiments/ryw_failure.py`、`experiments/ryw_partition.py`

每次迭代写入版本 `i`，随后立即读取同一文档。如果 `observed_version < written_version`，则记为 RYW violation。修正后 normal 版 RYW 已支持 `--uri` 和 `--causal-session`，结果文件也统一包含 `causaltrue/false`。

### 4.3 MR

脚本：`experiments/monotonic_reads.py`、`experiments/monotonic_reads_failure.py`、`experiments/monotonic_reads_partition.py`

每轮先写入新版本，再执行 Primary read 和 Secondary read。客户端维护 `max_seen`，如果后续读取版本低于 `max_seen`，则记为 MR violation。每轮包含两次读取，因此 1000 次迭代对应 2000 条读记录。

### 4.4 MW

脚本：`experiments/monotonic_writes.py`、`experiments/monotonic_writes_failure.py`、`experiments/monotonic_writes_partition.py`

每轮同一客户端连续写入两条有序记录 `(seq=2i-1, seq=2i)`，随后从 Secondary 读取可见序列。如果观察到后续序号可见但前序号缺失，则记为 MW violation。

### 4.5 WFR

脚本：`experiments/wfr.py`、`experiments/wfr_failure.py`、`experiments/wfr_partition.py`

实验流程：

1. Client A 写入前置事件 `v1`。
2. Client B 从 Primary 读取 `v1`。
3. Client B 写入依赖事件 `v2`，并记录 `depends_on=v1`。
4. Observer 从 Secondary 检查依赖关系。

旧脚本先查询 `v1` 再查询 `v2`，可能把两个不同时刻的状态拼接成 WFR violation。修正后脚本改为先观察 `v2`，只有当 `v2` 可见时再检查 `v1`，并写入 `observer_check_order=dependent_then_prerequisite`。若 `v2` 可见但 `v1` 不可见，则记为 WFR violation。该方法仍不是严格多文档快照，但误报风险低于旧版本。

## 5. 初步结果

本节结果用于展示趋势和报告结构。Normal 场景已使用修正后的脚本重新生成；failure 和 partition 仍来自已有 CSV，正式提交前建议在完整 Docker 环境中统一重跑这两类场景。

### 5.1 Normal 场景

| 模型 | 弱配置违背 | 弱配置违背率 | 强配置违背 | 强配置违背率 |
|---|---:|---:|---:|---:|
| RYW | 995 / 1000 | 99.50% | 0 / 1000 | 0.00% |
| MR | 98 / 2000 | 4.90% | 0 / 2000 | 0.00% |
| MW | 0 / 1000 | 0.00% | 0 / 1000 | 0.00% |
| WFR | 0 / 1000 | 0.00% | 0 / 1000 | 0.00% |

Normal 场景下，RYW 和 MR 已经明显暴露 weak configuration 的 stale read 问题。MW 未观察到违背。修正后的 WFR 在本机临时副本集重跑中未出现违背，可能因为单机复制延迟很小、观察窗口较窄。旧 CSV 中曾标记 WFR weak normal 为 `52/1000`，但该数字来自旧观察顺序，不作为本版 normal 结果。

### 5.2 Primary Failure 场景（已有 CSV）

| 模型 | 弱配置违背 | 弱配置违背率 | 强配置违背 | 强配置违背率 |
|---|---:|---:|---:|---:|
| RYW | 817 / 1000 | 81.70% | 0 / 1000 | 0.00% |
| MR | 292 / 2000 | 14.60% | 0 / 2000 | 0.00% |
| MW | 0 / 1000 | 0.00% | 0 / 1000 | 0.00% |
| WFR | 22 / 1000 | 2.20% | 0 / 1000 | 0.00% |

Failure 场景下，RYW 和 MR 弱配置违背率较高。需要注意，RYW 和 MR 在故障发生前也可能已有 stale read，因此最终报告应按 `phase=normal/failure/recovery` 分段统计，而不是把全部违背都归因于故障本身。

### 5.3 Network Partition 场景（已有 CSV）

| 模型 | 弱配置违背 | 弱配置违背率 | 强配置违背 | 强配置违背率 |
|---|---:|---:|---:|---:|
| RYW | 965 / 1000 | 96.50% | 0 / 1000 | 0.00% |
| MR | 330 / 2000 | 16.50% | 0 / 2000 | 0.00% |
| MW | 0 / 1000 | 0.00% | 0 / 1000 | 0.00% |
| WFR | 18 / 1000 | 1.80% | 0 / 1000 | 0.00% |

Network partition 对 RYW 和 MR 的影响最明显。被隔离的节点仍然运行，但不能与其他节点通信，因此客户端更容易观察到旧数据或复制延迟。WFR 的旧结果同样需要在修正后脚本上重新确认。

## 6. 结果分析

RYW 的 weak configuration 违背最明显。原因是 `writeConcern=1` 只要求 Primary 确认写入，而后续读取被路由到 Secondary。由于 MongoDB Secondary 异步复制 Primary oplog，写后立即读 Secondary 很可能读到旧版本。强配置使用 majority write/read concern 和 causal session，显著降低了这种风险。

MR 的违背来自客户端切换读取节点后观察到版本倒退。客户端先从 Primary 看到较新版本，再从复制滞后的 Secondary 读取，就可能读到更旧版本。Failure 和 partition 会加剧角色切换和复制滞后，因此 MR 弱配置违背率上升。

MW 在已有实验中均为 0。这不是异常，而是符合 MongoDB 架构预期：同一 Primary 将写入按 oplog 顺序复制到 Secondary，Secondary 即使落后，也通常不会先看到后写再缺少前写。因此报告中应写“在本实验条件下未观察到 MW 违背”，而不是刻意追求非零数字。

WFR 需要最谨慎。理论上，弱配置不提供完整因果一致性，因此可能出现 writes-follow-reads 违背。但旧脚本的观察顺序可能造成误报，因此最终结论应以修正后脚本重跑结果为准。如果修正后仍能观察到 `v2` 可见而 `v1` 不可见，则可以更有力地支持弱配置不保证 WFR。

整体趋势符合预期：弱配置提升可用性和低延迟，但允许 stale read；强配置通过 majority concern 和 causal session 提供更强客户端中心一致性。

## 7. 局限性与改进方向

1. 当前实验主要在单机 Docker 或单机多进程环境中运行，与真实多机网络延迟仍有差距。
2. Failure 和 partition 的已有结果来自历史 CSV；修正脚本后应在同一环境中统一重跑。
3. WFR 旧结果存在观察顺序误报风险。脚本已修正，但仍建议增加人工延迟或负载以提高可复现性。
4. Partition 脚本已支持自动发现 Docker network，但正式运行时仍应保存实际网络名、被隔离节点、恢复状态和 Primary 切换日志。
5. 每组主要为 1000 次迭代。为了更稳健，可以重复运行至少 3 次，并报告平均值、失败率、延迟中位数和 P95。
6. 故障场景应按故障前、故障中、恢复后三段分别统计，避免过度归因。

## 8. 复现实验说明

### 8.1 安装依赖

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 8.2 启动 Replica Set

```bash
docker-compose up -d
python setup/init_replica.py
```

如果第一次初始化时暂时没有 PRIMARY，可等待几秒后再次运行初始化脚本检查状态。

### 8.3 Normal 场景

弱配置：

```bash
python experiments/ryw.py --write-concern 1 --read-concern local --scenario normal --iterations 1000
python experiments/monotonic_reads.py --write-concern 1 --read-concern local --scenario normal --iterations 1000
python experiments/monotonic_writes.py --write-concern 1 --read-concern local --scenario normal --iterations 1000
python experiments/wfr.py --write-concern 1 --read-concern local --scenario normal --iterations 1000
```

强配置：

```bash
python experiments/ryw.py --write-concern majority --read-concern majority --scenario normal --iterations 1000 --causal-session
python experiments/monotonic_reads.py --write-concern majority --read-concern majority --scenario normal --iterations 1000 --causal-session
python experiments/monotonic_writes.py --write-concern majority --read-concern majority --scenario normal --iterations 1000 --causal-session
python experiments/wfr.py --write-concern majority --read-concern majority --scenario normal --iterations 1000 --causal-session
```

### 8.4 Failure 和 Partition 场景

Failure 弱配置示例：

```bash
python experiments/ryw_failure.py --scenario failure --write-concern 1 --read-concern local --iterations 1000
python experiments/monotonic_reads_failure.py --scenario failure --write-concern 1 --read-concern local --iterations 1000
python experiments/monotonic_writes_failure.py --scenario failure --write-concern 1 --read-concern local --iterations 1000
python experiments/wfr_failure.py --scenario failure --write-concern 1 --read-concern local --iterations 1000
```

Partition 弱配置示例：

```bash
python experiments/ryw_partition.py --scenario partition --write-concern 1 --read-concern local --iterations 1000
python experiments/monotonic_reads_partition.py --scenario partition --write-concern 1 --read-concern local --iterations 1000
python experiments/monotonic_writes_partition.py --scenario partition --write-concern 1 --read-concern local --iterations 1000
python experiments/wfr_partition.py --scenario partition --write-concern 1 --read-concern local --iterations 1000
```

如果自动发现网络失败，可以显式传入：

```bash
--network <compose_project>_mongodb-network
```

强配置在上述命令中加入：

```bash
--write-concern majority --read-concern majority --causal-session
```

## 9. 结论

本项目初步验证了 MongoDB tunable consistency 对 client-centric consistency 的影响。弱配置下，RYW 和 MR 显著容易违背，failure 和 network partition 会进一步放大 stale read 和版本倒退现象。MW 在已有实验条件下未观察到违背，符合 MongoDB 单 Primary 和 oplog 顺序复制机制。WFR 的最终数值需要以修正后的脚本重跑为准，但从理论上看，弱配置并不保证完整因果一致性。

强配置 `writeConcern=majority, readConcern=majority, causal session=true` 在已有结果中基本消除了四类一致性违背。因此，如果应用更重视低延迟和可用性，可以选择弱配置但需要容忍 stale read；如果应用需要客户端中心一致性，尤其是读己之写和因果一致性，应使用 majority concern 与 causal session。

## 10. 参考资料与 AI 使用说明

### 参考资料

1. MongoDB Documentation, "Replication": https://www.mongodb.com/docs/manual/replication/
2. MongoDB Documentation, "Replica Set Read and Write Semantics": https://www.mongodb.com/docs/manual/applications/replication/
3. MongoDB Documentation, "Read Concern": https://www.mongodb.com/docs/manual/reference/read-concern/
4. MongoDB Documentation, "Read Preference": https://www.mongodb.com/docs/manual/core/read-preference/
5. MongoDB Documentation, "Causal Consistency and Read and Write Concerns": https://www.mongodb.com/docs/manual/core/causal-consistency-read-write-concerns/
6. MongoDB Documentation, "Replica Set Elections": https://www.mongodb.com/docs/v8.0/core/replica-set-elections/

### AI 使用说明

本报告初稿由 OpenAI Codex 辅助生成。AI 辅助内容包括：读取项目要求、检查实验脚本、修正部分实验判定逻辑、汇总 CSV 结果、整理报告结构和生成初版文字。最终报告内容、实验结论和提交材料应由小组成员复核确认。
