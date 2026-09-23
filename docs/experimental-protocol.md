# 最终实验协议

本文件是代码与实验的测量约定，不是课程报告正文。最终入口为 `experiments`。

## 预先固定的设计

- 四模型：RYW、MR、MW、WFR；三场景：normal、failure、partition。
- 三配置：weak = w:1/local/causal:false；majority = majority/majority/causal:false；causal = majority/majority/causal:true。
- 每个组合 1,000 轮，三次独立运行，共 108 组、108,000 轮。每次运行使用新 collection、新客户端、新应用会话。
- 每个重复块内按 seed=5208 打乱 36 组顺序，串行执行；不并行操作同一集群。
- 故障第 200 轮在相互依赖的两个操作之间注入；第 600 轮开始前恢复。
- 正常阶段 199 轮，注入过渡 1 轮，故障阶段 399 轮，恢复阶段 401 轮。
- 每次运行前、后确认 1 PRIMARY + 2 SECONDARY；记录实际版本、配置、节点和代码 SHA-256。
- 自动 retryReads/retryWrites 关闭。选择服务器/连接超时 5 秒，socket 超时 10 秒，查询 maxTimeMS 和多数写 wtimeout 为 5 秒。
- MW/WFR 观察窗口为 1 秒，每次未看到目标写入后间隔 10 毫秒重试。窗口限制重试启动，单次网络请求可另外耗用其超时，因此不是严格 1 秒总截止时间。
- 小样本调试数据单列在 `results/pilot_final`；旧数据不混入最终统计。

## 实验预测

| 配置 | RYW / MR | MW / WFR |
|---|---|---|
| weak | secondary 可落后，可能观察到旧值或读取回退；并非每次必然发生 | 单 primary 顺序执行常可保持依赖；故障历史下不据此作普遍保证 |
| majority | 多数确认不使每个 secondary 立即追平，也未建立跨读写的显式因果约束；可能仍出现 RYW/MR 反例 | 多数提交可保护本实验的已确认依赖，但有限快照检验不能证明一般 MW/WFR |
| causal | 满足文档中 session + majority 读写的前提时，成功操作预期不违背；超时单独记录 | 预期未发现已多数提交依赖缺失；独立观察者仍使用固定测量策略 |

majority 与 causal 的应用参数仅差 causal session 开关；weak 与 majority 同时改变读写关注点，不单独归因给其中一个参数。

## 工作负载与判定

**RYW**：同一个应用会话向 primary 插入本轮不可变文档，成功确认后立即从 secondary 查询此文档。找不到已经确认的写入即为反例；请求失败是 error。

**MR**：独立生产者先更新一个整数版本寄存器；读者在一个会话内依次读 primary 和 secondary，跨轮维护成功读取的历史最大版本。小于历史最大值的读是回退。一行是一次生产者写入加一对读取；完整读对内任一回退使该行 violation。中途失败的行记 error，但已经成功的单次读取仍单列计数，不能藏掉已发生的回退。

**WFR**：A 使用独立 MongoClient 写 v1；B 在自己的会话中从 primary 读到 v1，然后向 primary 写入依赖 v1 的 v2。仅 B 的读、写共用 B 的应用会话。第三个客户端不接收 B 的会话时钟，在 secondary 的同一个 snapshot 中读 v1、v2。看见 v2 而没有 v1，记依赖缺失；未看到 v2 为 not_observed，不是 pass。

**MW**：同一个应用会话顺序写两个不可变文档，后者标记其已经确认的前驱；跨轮也保留上一条已确认写入。独立 secondary snapshot 同时查询当前两个文档和上一已确认前驱，只检查实际可见子文档的依赖边。某条子文档存在而其已确认前驱缺失，记依赖缺失；没有可检验的依赖边为 not_observed。写入超时不假定成功或失败，保留 error 和已确认状态。

### MW/WFR 的解释边界

观察者统一使用 `readConcern=snapshot`，与应用的 local/majority 设置分离。一次 find 在独立 snapshot session 内获取不可变文档，避免两次读跨越副本追赶造成假反例。每次重试新建快照，否则快照不会随复制推进。

这测量的是**同一个多数提交快照内的依赖完整性**，不是未提交 local 视图，也不是完整历史的 MW/WFR 证明。多数提交筛选和稳定单 primary 顺序复制会降低弱配置出现反例的机会。零违背必须同时给出有效观测数量，并写成“在本设计的观测范围内未发现依赖缺失”。不为了制造弱配置反例而篡改数据、延迟规则或丢弃重复。

## 故障模型

- failure：对依赖边界时的 primary 执行 `docker stop --time 5`，核实容器停止。它是节点停止，通常包含优雅关闭；不是突然断电或强制 crash。
- partition：将该 primary 从三个节点共有的 Docker bridge 网络断开，核实容器仍运行且网络附件消失。此操作可能同时影响宿主机访问，不能表述为仅切断服务器间通信。
- 注入命令同步执行，不添加固定 10 秒 sleep；管理操作本身仍产生没有业务请求的间隔。每轮记录 `latency_ms`、`management_ms`、`workload_ms`。恢复命令在该轮业务计时之前，起止保存在事件日志。
- `workload_ms` 扣除 Docker 注入控制时间，仍包含选路、客户端处理、日志和观察轮询，不是纯数据库服务耗时。
- 注入失败、恢复失败、缺少计划轮次都使该组 invalid；保留日志并停止矩阵。故障期间数据库错误保留，不当作一致性通过。
- finally 尝试恢复并检查健康；操作系统终止 Python 或断电可能阻止 finally 执行，需按具体日志目标手动恢复。

## 统计约定

`attempts = pass + violation + error + not_observed`。

`eligible = pass + violation`；`violation_rate = violation / eligible`；`coverage = eligible / attempts`。

无有效观察时违背率为 null，不是 0。总体和每阶段分别汇总。MR 另输出成功单次读与回退计数，包含错误行中此前已经成功的读。

每组先计算比例和延迟分位数，再按三次重复求均值与样本标准差。`aggregate.csv` 同时列出总计数，比例均值不是将所有轮次误当独立样本后的置信区间。三次重复足以检查基本稳定性，仍不足以支持精确总体推断。

`eligible_workload_p50/p95` 仅针对有效行；总体 `latency_p50/p95` 包含失败及注入控制耗时。四模型每轮操作数、读写类型和观察策略不同，只在同一模型内比较延迟；不将其排序为数据库原生操作速度。不把低错误率解释为完整可用性或吞吐量基准。

## 自动验收与证据

1. 离线测试覆盖误报、空观察、真实依赖缺失、跨轮读取历史、会话隔离、故障失败和恢复。
2. 小样本全矩阵用于运行链路检查；正式数据使用固定最终程序重新运行。
3. 正式矩阵必须全部完成、有有效观察、正常场景无数据库错误、causal 无已观测反例。验收规则不是弱配置必须出现违背。
4. `audit.py` 独立重读 CSV 并复核观测判定、统计、阶段、计划、最终拓扑、causal readConcern、snapshot 和故障状态证据，生成逐组及跨重复汇总。
5. 最终 SHA256SUMS 为原始文件和汇总提供完整性索引；保留本轮使用的源代码。

## 理论依据与 AI 使用

- [MongoDB 8.0 causal consistency and read/write concerns](https://www.mongodb.com/docs/v8.0/core/causal-consistency-read-write-concerns/)
- [MongoDB 8.0 snapshot read concern](https://www.mongodb.com/docs/v8.0/reference/read-concern-snapshot/)
- [PyMongo ClientSession](https://pymongo.readthedocs.io/en/stable/api/pymongo/client_session.html)

代码完善、回归测试和审计工具由 Codex 辅助完成；结果来自实际本机 MongoDB 实验。团队应理解测量范围并在课程报告中披露 AI 使用。
