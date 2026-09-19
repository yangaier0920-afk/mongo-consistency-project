# MongoDB Client-Centric Consistency Experiments

本仓库研究 RYW、MR、MW、WFR 在 weak、majority、causal 三种配置及正常、节点停止、网络分区场景下的表现。

## Repository map

| Path | Purpose |
|---|---|
| experiments/ | 统一实验、故障控制、审计及 test_consistency.py |
| results/primary/ | 报告主结果：108 组、108000 轮 |
| results/replication/ | 后续完整复现：108 组、108000 轮 |
| results/pilot/ | 预检：36 组、1080 轮，不进入正式统计 |
| report/ | 中文正文、图表和复现对照；尚未生成最终 PDF |
| docs/ | 复现、实验协议、课程要求与 AI 使用说明 |
| scripts/ | 命名迁移检查、历史批次审计、GitHub 上传 |
| provenance/ | 历史运行源码，保持原始指纹 |
| validation/ | 本次整理的验证记录 |

## Quick verification

Python 3.11+，在仓库根目录执行：

```powershell
python verify_release.py
python scripts/verify_migration.py
python -m unittest experiments.test_consistency -v
python scripts/audit_published.py
```

离线验证不需要 Docker。安装及完整重跑见 [复现说明](docs/reproduction.md)。旧结果使用历史源码审计，新运行使用规范命名后的源码审计；不修改历史指纹来伪装二者完全相同。

## Findings and report alignment

两次完整实验均出现 weak/majority 的 RYW/MR 反例，causal 未检测到违背；MW/WFR 的零反例仅适用于当前多数提交快照依赖检测范围。错误与未充分观察单独统计。主报告引用 primary，复现对照引用 replication，路径索引见 results/index.json。

报告状态见 [report/README.md](report/README.md)，复现比较见 [report/replication.zh.md](report/replication.zh.md)。PDF、组员姓名学号及最终英文版本仍需完成。课程要求 PDF 与代码在 2026-09-27 前提交 Canvas，GitHub 不替代 Canvas。

## Upload

Windows 双击 UPLOAD_TO_GITHUB.cmd，使用已配置的小组仓库地址（需要 Git 和已配置的 GitHub 登录）。脚本创建独立提交分支，不直接改 main；推送后在 GitHub 比较并合并。详细行为见 docs/github-upload.md。
