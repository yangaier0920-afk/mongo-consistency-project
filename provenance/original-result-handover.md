# 最终实验验收与结果索引

状态：**正式批次完整完成，独立审计通过，可作为小组提交与后续报告的数据基础。**

本文件是实验交付说明，不是报告正文。实验在 2026-09-18 本机运行完成，2026-09-19 完成全量审计与交付核验。

## 唯一正式批次

`results/final/matrix_20260918T135313Z_ee2bf4a7/`

- 4 模型 × 3 场景 × 3 配置 × 3 次重复 = **108 组**，每组 1,000 轮，共 **108,000 轮**。
- **107,898** 次有效观察，**422** 次已观测违背，**38** 次操作错误，**64** 次未充分观察。违背包含在有效观察中，不能与有效观察重复相加。
- 26 项离线回归测试通过；此前 36 组、1,080 轮开发小样本全部通过运行与观测复核，小样本不纳入正式统计。
- 108 组初始/最终健康检查、故障组注入/恢复证据、逐条判定、分阶段统计、命令关注点及代码指纹均通过审计。
- 最终集群复核：mongo1 为 PRIMARY，mongo2/mongo3 为 SECONDARY。角色后续可能因选举改变，以现场检查为准。
- 实测 Python 3.11.5、PyMongo 4.18.0、MongoDB 8.0.29。Compose 镜像固定为 mongo:8.0.29；本次复用的容器原标签为 mongo:8.0，实际服务器版本和镜像 ID 已记录。

## 结果概览

下表是每个模型/场景的三次重复合计，显示 **违背数 / 有效观察数**。这是计数索引；跨重复的比例均值、样本标准差和延迟请使用 aggregate.csv，而不要把所有循环当作独立重复。

| 模型 | 场景 | weak | majority | causal |
|---|---|---:|---:|---:|
| RYW | normal | 10 / 3000 | 2 / 3000 | 0 / 3000 |
| RYW | failure | 335 / 3000 | 5 / 3000 | 0 / 3000 |
| RYW | partition | 6 / 2997 | 5 / 2997 | 0 / 2997 |
| MR | normal | 4 / 3000 | 1 / 3000 | 0 / 3000 |
| MR | failure | 50 / 3000 | 2 / 3000 | 0 / 3000 |
| MR | partition | 1 / 2997 | 1 / 2997 | 0 / 2995 |
| MW | normal | 0 / 3000 | 0 / 3000 | 0 / 3000 |
| MW | failure | 0 / 3000 | 0 / 3000 | 0 / 3000 |
| MW | partition | 0 / 2957 | 0 / 2997 | 0 / 2997 |
| WFR | normal | 0 / 3000 | 0 / 3000 | 0 / 3000 |
| WFR | failure | 0 / 3000 | 0 / 3000 | 0 / 3000 |
| WFR | partition | 0 / 2973 | 0 / 2997 | 0 / 2997 |

MR 的一轮为一对读取，不是单次读。MW/WFR 的“违背”指本实验定义的多数提交快照内依赖缺失。

| 配置 | 尝试数 | 有效观察 | 操作错误 | 未充分观察 | 违背 |
|---|---:|---:|---:|---:|---:|
| weak | 36000 | 35924 | 12 | 64 | 406 |
| majority | 36000 | 35988 | 12 | 0 | 16 |
| causal | 36000 | 35986 | 14 | 0 | 0 |

## 已核实的结论与解释边界

1. causal 在 35,986 次有效观察中未发现违背，另有 14 次操作错误。这支持本次设计下的预期行为，不能把错误当作通过，也不能以有限样本证明所有未来历史。
2. weak 与 majority 的 RYW/MR 均出现过反例。majority 组没有启用 causal session，多数确认不能替代本实验中跨操作的因果约束。
3. WFR/MW 各配置都没有观测到快照依赖缺失，而且拥有充分的有效样本。WFR weak partition 有 24 次、MW weak partition 有 40 次未观察到目标写入，均已排除出违背率分母。它们不是旧版“从未看到目标文档却算通过”的结果。
4. MW/WFR 观察者固定使用多数提交 snapshot，避免非原子双读误报，但也限定了观测范围。结果不能推广为弱配置在未提交 local 视图或所有故障历史下都保证 MW/WFR。
5. 不应简单写“故障提高了违背率”。例如 weak RYW failure 三次重复的违背数分别为 62、76、197，其中故障前分别为 61、3、196，故障期分别为 0、73、0，恢复期分别为 1、0、1；表现随运行状态与选路变化。majority RYW failure 的 5 次反例全部发生在注入前。
6. failure 是同步执行的 docker stop，通常允许优雅关闭；partition 是断开整张 Docker 网络。它们不等同于突然断电、仅切断服务器互联，或持续并发负载的可用性基准。
7. 每个模型的工作量不同，延迟只在同一模型内比较。记录含客户端/日志/轮询开销；eligible_workload 指标扣除了注入控制命令耗时，仍不是纯数据库服务时间。

## 查阅与复核

- [独立审计结论](results/final/matrix_20260918T135313Z_ee2bf4a7/audit.json)
- [每组、每阶段统计](results/final/matrix_20260918T135313Z_ee2bf4a7/per_run.csv)
- [跨三次重复的均值和标准差](results/final/matrix_20260918T135313Z_ee2bf4a7/aggregate.csv)
- [实验顺序和固定参数](results/final/matrix_20260918T135313Z_ee2bf4a7/plan.json)
- [原始文件及汇总指纹](results/final/matrix_20260918T135313Z_ee2bf4a7/SHA256SUMS.json)

每组子目录保留 metadata.json、operations.csv、events.jsonl.gz、summary.json。压缩日志包含实际节点、读写关注点和故障证明。

在项目根目录验证已发布文件是否变化（无需 Docker 或第三方包）：

```powershell
python verify_release.py
```

重新从逐条记录复算与审计（无需启动数据库）：

```powershell
.\.venv\Scripts\python.exe -m experiments_v2.audit results/final/matrix_20260918T135313Z_ee2bf4a7
```

全套重跑、环境搭建与故障恢复命令见 [README.md](README.md)，设计与预测见 [EXPERIMENT_PROTOCOL.md](EXPERIMENT_PROTOCOL.md)。

## GitHub 提交

提交 experiments_v2/ 中的代码和 README、docker-compose.yml、requirements.txt、.gitattributes、.gitignore、README.md、README_legacy.md、EXPERIMENT_PROTOCOL.md、FINAL_RESULTS.md、verify_release.py、RELEASE_SHA256.json，以及上面指定的完整正式结果目录和 results/final_unit_tests.log。无需提交 .venv、__pycache__、Docker 数据卷。

旧实验与旧结果可以保留在仓库中供追溯，但不应与本批次混为“最终结果”。report_draft.md 尚未按新结果改写，不能直接作为最终报告。本轮没有修改报告正文，也没有代你执行 GitHub push。
