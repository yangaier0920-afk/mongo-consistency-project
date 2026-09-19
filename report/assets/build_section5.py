"""Rebuild section 5 and its figures from the audited experiment JSON files.

Run from the repository root with Python and matplotlib installed:
    python assets/build_section5.py
Only report files are written; published experiment files are read-only inputs.
"""
import collections
import hashlib
import json
import re
import statistics as st
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BATCH = ROOT / 'results/primary/matrix_20260918T135313Z_ee2bf4a7'
ITEMS = json.loads((BATCH / 'matrix_summary.json').read_text(encoding='utf-8'))
AUDIT = json.loads((BATCH / 'audit.json').read_text(encoding='utf-8'))
assert AUDIT['audit_passed'] and len(ITEMS) == 108
PROFILES = ('weak', 'majority', 'causal')
MODELS = ('ryw', 'mr', 'mw', 'wfr')


def runs(model, scenario, profile):
    result = sorted([x for x in ITEMS if (x['model'], x['scenario'], x['profile']) ==
                     (model, scenario, profile)], key=lambda x: x['repeat'])
    assert len(result) == 3
    return result


def stats(model, scenario, profile, phase='overall'):
    return [x['summary']['overall'] if phase == 'overall' else x['summary']['by_phase'][phase]
            for x in runs(model, scenario, profile)]


def avg_sd(values, scale=1):
    values = [v*scale for v in values]
    return f'{st.mean(values):.3f} ± {st.stdev(values):.3f}'


def count_rate(model, scenario, profile):
    ss = stats(model, scenario, profile)
    return (f"{sum(s['violations'] for s in ss)}/{sum(s['eligible'] for s in ss)}；"
            + avg_sd([s['violation_rate'] for s in ss], 100))


available = {f.name for f in font_manager.fontManager.ttflist}
font = next((f for f in ('Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC') if f in available), 'DejaVu Sans')
plt.rcParams.update({'font.family': font, 'axes.unicode_minus': False, 'font.size': 11,
                     'axes.spines.top': False, 'axes.spines.right': False, 'pdf.fonttype': 42})
colors = ['#3073ad', '#dc8736', '#29947e']

# Each point/line is one complete run; percentages use the correct phase denominator.
fig, axes = plt.subplots(1, 2, figsize=(12, 4.4))
phases = ('normal', 'fault', 'recovery')
for ax, model in zip(axes, ('ryw', 'mr')):
    for r, item in enumerate(runs(model, 'failure', 'weak')):
        vals = [100*item['summary']['by_phase'][p]['violation_rate'] for p in phases]
        ax.plot(range(3), vals, marker=['o', 's', '^'][r], color=colors[r], label=f'重复 {r+1}', linewidth=1.6)
        for x, value in enumerate(vals):
            if value > 1:
                ax.annotate(f'{value:.2f}%', (x, value), xytext=(5, 5), textcoords='offset points', fontsize=9)
    ax.set_xticks(range(3), ['注入前\n199 轮', '故障期间\n399 轮', '恢复阶段\n401 轮'])
    ax.set_ylabel('阶段违背率（%）')
    ax.set_title(f'{model.upper()}：weak，节点停止')
    ax.set_ylim(bottom=-.5)
    ax.grid(axis='y', alpha=.2)
    ax.legend(frameon=False, fontsize=9)
fig.tight_layout()
for ext in ('png', 'pdf'):
    fig.savefig(OUT/f'fig4_failure_phases.{ext}', dpi=180, bbox_inches='tight')
plt.close(fig)

# Display all three P95 observations, alongside mean ± sample SD.
fig, axes = plt.subplots(2, 2, figsize=(11, 7.4))
for ax, model in zip(axes.flat, MODELS):
    for j, profile in enumerate(PROFILES):
        ys = [s['eligible_workload_p95_ms'] for s in stats(model, 'normal', profile)]
        ax.scatter([j-.11, j, j+.11], ys, s=34, color=colors[j], alpha=.75, zorder=3)
        ax.errorbar(j, st.mean(ys), yerr=st.stdev(ys), fmt='_', markersize=19,
                    color='#242d37', capsize=6, linewidth=1.3, zorder=4)
    ax.set_xticks(range(3), PROFILES)
    ax.set_ylabel('有效观察轮次的 P95（ms）')
    ax.set_title(model.upper())
    ax.set_ylim(bottom=0)
    ax.grid(axis='y', alpha=.2)
fig.tight_layout()
for ext in ('png', 'pdf'):
    fig.savefig(OUT/f'fig5_normal_p95.{ext}', dpi=180, bbox_inches='tight')
plt.close(fig)

text = '''## 5. 实验结果

### 5.1 正式批次与观察覆盖

本节仅使用统一代码生成并通过审计的正式批次，共 108 组、108,000 轮，所有计划组合均完成。按第 4 节分类，其中 107,898 轮具有有效观察，422 轮检测到违背，38 轮发生操作错误，64 轮未充分观察。违背轮次包含在有效观察之内；其余 107,476 个有效轮次未检测到违背。表 9 按配置汇总数据完整性，合计数用于说明样本构成，不将不同模型混合后的比例解释为一种通用一致性指标。

**表 9. 各配置的正式样本构成（合计四模型、三场景和三次重复）**

| 配置 | 尝试轮数 | 有效观察 | 违背 | 操作错误 | 未充分观察 | 有效覆盖率 |
|---|---:|---:|---:|---:|---:|---:|
'''
totals = {}
for profile in PROFILES:
    ss = [x['summary']['overall'] for x in ITEMS if x['profile'] == profile]
    row = {k: sum(s[k] for s in ss) for k in ('attempts', 'eligible', 'violations', 'errors', 'not_observed')}
    totals[profile] = row
    text += '| '+profile+' | '+' | '.join(f'{row[k]:,}' for k in row)+f" | {100*row['eligible']/row['attempts']:.3f}% |\n"
assert sum(r['attempts'] for r in totals.values()) == 108000
assert sum(r['violations'] for r in totals.values()) == 422
text += '''
正常和节点停止场景均未记录操作错误或未充分观察。全部 38 次错误与 64 次未充分观察来自网络分区场景，具体分布见第 5.4 节。causal 配置在 35,986 次有效观察中未检测到违背，另有 14 次操作错误；这里的零违背不包含这些错误轮次。

### 5.2 RYW 与 MR 的配置对照

表 10 汇总 RYW 和 MR 的结果。每个单元格先给出三次重复的“违背数 / 有效观察数”，再给出逐次运行违背率的均值 ± 样本标准差，后者的单位均为百分比。标准差描述运行间变动，不是置信区间。

**表 10. RYW/MR 的违背计数与跨重复违背率（%）**

| 模型 | 场景 | weak | majority | causal |
|---|---|---|---|---|
'''
for model in ('ryw', 'mr'):
    for scenario in ('normal', 'failure', 'partition'):
        text += '| '+model.upper()+' | '+scenario+' | '+' | '.join(count_rate(model, scenario, p) for p in PROFILES)+' |\n'
text += '''
正常场景中，RYW 的 weak 与 majority 分别观察到 10 次和 2 次违背；MR 则分别为 4 次和 1 次。两个模型的 causal 组均为零。由此可见，在本次工作负载中，采用 majority 读写关注点仍不能排除这些客户端读取反例。

节点停止场景中，weak 的整组违背数更高：RYW 为 335 次，MR 为 50 次。不过，整组包括注入前、故障期间和恢复阶段，不能仅凭这些总数将反例全部归因于节点停止。网络分区场景也没有呈现相同的计数变化趋势，其阶段分布需要另行检查。

MR 在表 10 中的分母是完整读对。另按成功单次读取统计，正常场景的 weak 与 majority 分别为 4/6,000 和 1/6,000，即约 0.067% 和 0.017%；不能将这些比例与表中的读对违背率混用。

### 5.3 节点停止场景的阶段与重复差异

表 11 展示 weak 配置下的逐次运行结果。每个阶段列显示违背次数，列头给出该阶段每次运行的有效轮数；第 200 轮的过渡阶段在这些运行中均有一条有效记录且没有违背，故不另设列。

**表 11. weak 节点停止实验的分阶段违背次数**

| 模型 | 重复编号 | 注入前（199 轮） | 故障期间（399 轮） | 恢复阶段（401 轮） | 全组违背次数 |
|---|---:|---:|---:|---:|---:|
'''
for model in ('ryw', 'mr'):
    for item in runs(model, 'failure', 'weak'):
        counts = [item['summary']['by_phase'][p]['violations'] for p in phases]
        text += f"| {model.upper()} | {item['repeat']} | "+' | '.join(map(str, counts))+f" | {item['summary']['overall']['violations']} |\n"
text += '''
RYW 的三次整组违背次数分别为 62、76、197，但对应故障期间的违背次数为 0、73、0。合计 335 次反例中，260 次发生在注入前，73 次发生在故障期间，2 次发生在恢复阶段。第三次运行的大部分反例发生在注入前，而第二次运行的反例主要集中在故障期间。

MR 呈现不同分布：50 次反例中，49 次发生在故障期间，其中第一次运行贡献了 45 次。两种模型都存在明显的运行间差异，说明只展示跨重复均值会掩盖反例出现的时间位置。

![图 4. weak 节点停止实验的逐次运行阶段违背率](assets/fig4_failure_phases.png)

**图 4.** 每条线表示一次运行，分母为对应阶段的有效轮数；连线用于对照阶段均值，不表示阶段内连续的瞬时变化。两个面板的纵轴尺度不同。没有违背的过渡阶段未绘出。

majority 配置下，RYW 的 5 次反例和 MR 的 2 次反例均出现在故障注入前，故障期间及恢复阶段未检测到违背。causal 则在各阶段均未检测到违背。这些观察需要与实际节点和复制状态结合解释，相关机制放在第 6 节讨论。

### 5.4 网络分区、操作错误与依赖观察

表 12 列出网络分区的样本构成。每个模型和配置均尝试 3,000 轮；单元格依次表示“有效观察 / 操作错误 / 未充分观察”。

**表 12. 网络分区场景的有效样本与未完成观察**

| 模型 | weak | majority | causal |
|---|---|---|---|
'''
for model in MODELS:
    cells = []
    for profile in PROFILES:
        ss = stats(model, 'partition', profile)
        cells.append(' / '.join(str(sum(s[k] for s in ss)) for k in ('eligible', 'errors', 'not_observed')))
    text += '| '+model.upper()+' | '+' | '.join(cells)+' |\n'
text += '''
在 RYW 和 MR 中，网络分区场景观测到的全部一致性反例都发生在注入前；过渡、故障和恢复阶段的有效观察中未检测到反例。这并不表示故障期间每次操作均成功：全体模型合计的 38 次操作错误中，18 次发生在过渡阶段，18 次发生在故障阶段，2 次发生在恢复阶段。

原始记录的异常类型包括 21 次 `_OperationCancelled`、15 次 `NetworkTimeout`、1 次 `ExecutionTimeout` 和 1 次 `NotPrimaryError`。这些是程序记录的错误类别，不单凭类别推断每次异常的完整根因；它们在本节用于说明请求未完成，不能作为一致性通过的样本。

MW 和 WFR 在全部场景与配置中均未观察到多数提交快照内的依赖缺失。正常和节点停止场景中，每个模型、每个配置都有 3,000 次有效观察。分区场景中，weak MW 的有效覆盖率为 2,957/3,000，即 98.567%；weak WFR 为 2,973/3,000，即 99.100%。其 40 次和 24 次未充分观察均单独保留，其余配置的 MW/WFR 分区组各有 2,997 次有效观察。

因此，MW/WFR 的零违背结果建立在实际可判定的样本之上，但结论仍是“在已采样的多数提交快照中未发现依赖缺失”。它不包含未充分观察的轮次，也不覆盖未提交 local 状态或全部操作历史。

### 5.5 同一模型内的延迟比较

表 13 使用正常场景比较配置，避免将故障控制时间混入主要延迟对照。表中数据为有效观察轮次的 workload_ms：先在每次运行中计算 P50/P95，再报告三次运行分位数的均值 ± 样本标准差，单位为毫秒。

**表 13. 正常场景的有效观察轮次延迟（ms）**

| 模型 | 配置 | P50：均值 ± 标准差 | P95：均值 ± 标准差 |
|---|---|---:|---:|
'''
for model in MODELS:
    for profile in PROFILES:
        ss = stats(model, 'normal', profile)
        text += f'| {model.upper()} | {profile} | '+ ' | '.join(avg_sd([s[k] for s in ss]) for k in
                ('eligible_workload_p50_ms', 'eligible_workload_p95_ms'))+' |\n'
text += '''
RYW 和 MR 中，weak 的 P50 低于另外两种配置；majority 与 causal 的 P50 较接近。MW 和 WFR 则没有出现同样的顺序：weak 的 P50 分别为 26.361 ms 和 28.979 ms，均高于相应的 majority 与 causal 组。

逐轮记录显示，正常场景中 weak MW 有 2,683/3,000 个有效轮次进行了不止一次快照查询，weak WFR 为 2,950/3,000；majority 与 causal 的 MW 对应计数分别为 2 和 1，WFR 则均为 0。完整轮次计时包含观察轮询，因此这些数据不能被直接解释为弱配置的单次写入更慢，也不支持“弱配置在四种工作负载中都具有更低延迟”的概括。

![图 5. 正常场景各次运行的有效观察轮次 P95](assets/fig5_normal_p95.png)

**图 5.** 彩色点为三次运行各自的 P95，黑色横线及误差线表示其均值 ± 样本标准差，不是置信区间。各面板纵轴尺度不同，应在同一模型内比较配置，不能根据跨面板高度排序模型性能。

MW causal 的 P95 尤其需要保留运行间差异：三次分别为 73.179、18.042、18.232 ms，形成 36.484 ± 31.779 ms 的汇总。较高均值主要受第一次运行影响，不能写成每次运行都存在同等程度的尾部延迟。

故障场景的完整延迟表保留在逐组与跨重复汇总中。作为补充，weak MW 在节点停止和分区场景下的三次运行 P95 分别处于 112.237—112.778 ms、112.536—112.911 ms；weak WFR 分别为 110.734—112.804 ms、110.714—113.603 ms。这些数值仍描述包含观察等待的完整有效轮次，且排除了 error/not_observed 行，不能代替包含全部请求的可用性或服务中断统计。

本节结果为下一节提供三项需要解释的观察：majority 与 causal 的 RYW/MR 表现不同；故障场景的整组计数与故障期间反例并非一一对应；MW/WFR 的零违背和耗时均需结合快照测量范围及观察等待理解。

'''

# Independently inspect the underlying rows for claims not stored in matrix_summary.
errors = collections.Counter()
error_phases = collections.Counter()
polls = collections.defaultdict(lambda: [0, 0])
import csv
for item in ITEMS:
    with (BATCH/item['folder']/'operations.csv').open(encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream):
            details = json.loads(row['details_json'])
            if row['verdict'] == 'error':
                errors[details['error_type']] += 1
                error_phases[(item['scenario'], row['phase'])] += 1
            if item['scenario'] == 'normal' and 'snapshot_polls' in details and row['verdict'] in ('pass', 'violation'):
                key = (item['model'], item['profile'])
                polls[key][0] += 1
                polls[key][1] += details['snapshot_polls'] > 1
assert dict(errors) == {'_OperationCancelled': 21, 'NetworkTimeout': 15, 'ExecutionTimeout': 1, 'NotPrimaryError': 1}
assert error_phases == {('partition', 'transition'): 18, ('partition', 'fault'): 18, ('partition', 'recovery'): 2}
assert polls[('mw', 'weak')] == [3000, 2683] and polls[('wfr', 'weak')] == [3000, 2950]
evidence = {'batch': str(BATCH.relative_to(ROOT)), 'profile_totals': totals,
            'error_types': dict(errors), 'error_phases': {str(k): v for k, v in error_phases.items()},
            'normal_snapshot_polls': {str(k): {'eligible': v[0], 'multiple_polls': v[1]} for k, v in polls.items()},
            'matrix_summary_sha256': hashlib.sha256((BATCH/'matrix_summary.json').read_bytes()).hexdigest()}
(OUT/'section5_evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
(OUT/'section5.md').write_text(text, encoding='utf-8')
draft_path = ROOT/'report/report.zh.md'
draft = draft_path.read_text(encoding='utf-8')
draft = draft.replace('当前已起草第 1—4 节', '当前已起草第 1—5 节')
if '## 5. 实验结果\n' in draft:
    start = draft.index('## 5. 实验结果\n')
    end_match = re.search(r'^## ', draft[start+len('## 5. 实验结果\n'):], flags=re.M)
    assert end_match
    end = start+len('## 5. 实验结果\n')+end_match.start()
    draft = draft[:start]+text+draft[end:]
else:
    draft = draft.replace('## 参考文献（随章节补充）', text+'## 参考文献（随章节补充）')
note = '第 5 节由 assets/build_section5.py 从正式批次 JSON 汇总生成表格和图 4—5，并读取逐轮记录核对错误类型、阶段和快照轮询次数；图形另保存 PDF 版本。没有改写实验原始数据。'
if note not in draft:
    draft += '\n'+note+'\n'
draft_path.write_text(draft, encoding='utf-8')
print('Updated section 5; generated two figures (PNG/PDF) and evidence JSON.')
