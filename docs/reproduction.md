# Reproduction (Windows PowerShell)

Python 3.11+、Docker Desktop Linux engine。历史实际版本为 Python 3.11.5、PyMongo 4.18.0、MongoDB 8.0.29。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

hosts 需有 `127.0.0.1 mongo1 mongo2 mongo3`。先检查 `docker ps -a`；已有三个实验容器则复用，不创建同名容器或删除卷。无容器时执行 `docker compose up -d`，然后：

```powershell
.\.venv\Scripts\python.exe -m experiments.prepare
.\.venv\Scripts\python.exe -m unittest experiments.test_consistency -v
.\.venv\Scripts\python.exe -m experiments.matrix --models ryw mr mw wfr --scenarios normal failure partition --profiles weak majority causal --iterations 30 --fault-at 10 --recover-at 20 --repeats 1 --seed 5208 --output results/reruns/pilot
```

成功后审计打印出的实际目录：`python -m experiments.audit <actual-matrix-directory>`（使用虚拟环境 Python）。随后正式重跑：

```powershell
.\.venv\Scripts\python.exe -m experiments.matrix --models ryw mr mw wfr --scenarios normal failure partition --profiles weak majority causal --iterations 1000 --fault-at 200 --recover-at 600 --repeats 3 --seed 5208 --output results/reruns/full
```

新批次用 `experiments.audit` 审计，要求 audit_passed=true、runs=108、attempts=108000、代码匹配全部为 true；最后再运行 experiments.prepare 检查一主两从。一次只运行一个实验进程，不关闭 Docker 或让电脑睡眠。故障场景主动停止或隔离当前 primary，再恢复；错误被保留，不以重试隐藏。异常停止后依据日志的实际目标和网络恢复，不默认 mongo1，不删数据卷。

已发布三批次用 `python scripts/audit_published.py` 审计；它调用保留的历史审计器并重建同一批次的汇总。脚本命名迁移不改变实验算法，但源码指纹会变化，所以历史原件与当前入口明确区分。新结果写入 reruns，不覆盖主结果；计数和延迟不要求逐项相同。
