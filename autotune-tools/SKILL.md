---
name: autotune-tools
description: 使用 bundled CLI autotune-tools 管理 autotune workspace 时使用。
---

# autotune-tools

使用本 skill bundled 的工具 `scripts/autotune-tools` 管理 autotune workspace，通过子命令的 `--help` 查看参数。

## init-workspace

`init-workspace <repo> <workspace> --baseline-meta <path> --metric NAME=VALUE [--metric ...] [--max-attempts N] [--ref URL REVISION NAME ...] < prompt.md`

- stdin 用户原始请求不能为空或纯空白，原样保存到 `user_prompt.md`。
- `--baseline-meta` 必填：校验 `schema_version`、evaluation、metrics、protected_files；在 best worktree 上核对受保护文件 SHA-256；复制为 workspace 内 `baseline_meta.json`。
- `--metric NAME=VALUE` 至少一次：NAME 必须属于 meta 的 `metrics.benchmark`；VALUE 为该指标在当前 baseline 代码上的实测值。写入 `target-metric.json`（含 unit、direction、baseline；可选 `max_attempts`）。
- `--ref` 可重复，最多 8 个并行下载并递归初始化子模块。

## get-context

`get-context <workspace>`：在锁下读取状态，将 Markdown 写到 **stdout**（供生成 Attempt plan 前必读）：

1. 当前 best 相对初始 baseline 的监控指标变化  
2. `user_prompt.md`  
3. 最近至多 10 次 Attempt 的指标相对 baseline 变化、summary、directory  
4. `reference/README.md`（缺失时注明）

## new-attempt / complete-attempt

- `new-attempt`：从 best 创建候选；若 `target-metric.json` 含 `max_attempts` 且 Attempt 总数已达上限则拒绝。
- `complete-attempt`：必须传入恰好覆盖 `target-metric` 全部键的 `--metric` JSON、非空 `--summary`，stdin 读 detail。
  - 默认成功：相对当前 best 的参考指标做帕累托检查后 squash 合入。参考指标取「SquashCommitSHA == best HEAD」的最近一次 completed Attempt；若尚无合入则用 `target-metric` 中的 baseline 值。
  - 至少一个监控指标必须提升。允许至多一个指标相对劣化 ≤1%，且同时另有指标提升。更差或多项劣化需加 `--allow-regression`。
  - `--fail` 仅记录失败，仍要求 metric 齐全；允许 best 已前进。成功合入要求双方干净且 best 未偏离候选起始提交。

修改 workspace 数据库、`best` 或结果文件的工具统一持有 `.autotune.lock` 的 `flock` 排他锁。锁文件持久保留；初始化失败后仅含锁文件的目录可以重试初始化。
