---
name: tune-loop
description: 执行 CUDA kernel 调优循环时使用，生成候选实现、验证正确性、评测性能并记录每次尝试。
---

# 调优循环

## 概况

循环根据 workspace 内的 baseline 评测与 `target-metric.json` 执行 Attempt，直到监控指标达标或达到 `max_attempts`。workspace 由 [autotune-tools](../autotune-tools/SKILL.md) 初始化；评价体系来自其中的 `baseline_meta.json`（verify/benchmark 命令、指标定义、受保护文件），不是临时手写命令。

## 优化目标与评价体系

- 监控哪些 benchmark 指标、各指标 baseline 实测值、可选循环上限：见 `target-metric.json`（init 时由用户目标从 meta 的 `metrics.benchmark` 中选出）。
- 每次 Attempt：在候选 repo 根目录按 `baseline_meta.json` 的 `evaluation.verify` / `evaluation.benchmark` 运行评测；不要改写 `protected_files` 中的路径。
- 合入必须帕累托改进当前 best（工具在 `complete-attempt` 中强制检查）。单指标 ≤1% 劣化且另有提升可通过；更大劣化需 `--allow-regression`。

## Workspace 目录

```
workspace/
├── .autotune.lock
├── user_prompt.md
├── baseline                  # 初始 HEAD 的完整 commit SHA
├── baseline_meta.json        # init 时复制的评测元信息
├── target-metric.json        # 本次监控指标、baseline 值、可选 max_attempts
├── autotune.db
├── best/
├── reference/
│   └── README.md             # 入选参考项目简介
└── attempts/
    └── attempt-00001/
        ├── plan.md
        ├── result.json
        ├── result.md
        └── repo/
```

- `user_prompt.md`：调优目标与约束的原始依据。
- `baseline_meta.json` / `target-metric.json`：评测契约与监控范围；`complete-attempt` 的 `--metric` 键必须与 `target-metric` 完全一致。
- `best`：固定 worktree，接受的 Attempt squash 合入。
- `reference`：`--ref` 下载的参考项目；`README.md` 由初始化流程填写。
- Attempt 的 `BaseCommitSHA` 记录创建时的 best HEAD。

## 优化 Attempt

1. 若 `target-metric.json` 已达 `max_attempts`，停止循环。
2. **生成 plan 之前**必须运行 `autotune-tools get-context <workspace>`，根据 stdout 的 Markdown（workspace 绝对路径与目录结构、best 相对 baseline、user prompt、近 10 次 Attempt、reference README）撰写计划；不可凭记忆跳过。计划须写明拟借鉴的参考项目/技术点，以及预期影响的监控指标。
3. 将计划 stdin 传给 `new-attempt <workspace>`。
4. 在候选 repo 中改代码；先跑 verify，失败则 `--fail` 结束。再跑 benchmark，收集 `target-metric` 中全部指标。
5. 成功时提交全部待接受修改并使 repo 干净，执行  
   `complete-attempt <workspace> <id> --metric '{...}' --summary '...' < detail.md`。  
   详情须含 verify/benchmark 结果。若帕累托不通过且仍要合入，加 `--allow-regression`。
6. best 已前进时：从新 best 新建 Attempt 并重新验证，不直接合入旧结果。
7. 失败用同一命令加 `--fail`，仍需齐全 metric、summary 与 detail。

结果写入数据库与 `result.json` / `result.md`。创建和合入使用 `.autotune.lock`。旧版布局不再支持。
