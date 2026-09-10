---
name: tune-loop
description: 执行 CUDA kernel 调优循环时使用，生成候选实现、验证正确性、评测性能并记录每次尝试。
---

# 调优循环


## 概况

循环根据用户指定的 baseline 和优化目标执行一次次 Attempt，直到目标达成或达到循环次数限制。用户指定一个 workspace，通过 [autotune-tools](../autotune-tools/SKILL.md) 初始化并记录调优过程。


## 优化目标

用户可以指定多个优化目标。每一次尝试，可以是对单个目标的优化，也可以是对多个目标的综合优化。但优化过程是帕累托最优的。也就是每一次尝试不要降低其他目标的性能。


## Workspace 目录

Workspace目录中，包含
```
workspace/
├── .autotune.lock
├── user_prompt.md            # 用户原始请求
├── autotune.db
├── baseline                  # 文件：初始化时用户仓库 HEAD 的完整 commit SHA
├── best/                     # 固定的 detached Git worktree，累积 squash 提交
├── reference/
└── attempts/
    └── attempt-00001/
        ├── plan.md
        ├── result.json       # status, metrics, summary, details, squash_commit_sha
        ├── result.md         # summary and details
        └── repo/             # 从创建时的 best HEAD 派生的 detached worktree
```


其中

- user_prompt.md 在初始化时从 stdin 读取非空的用户原始请求并原样写入，作为调优目标与约束的原始依据。
- autotune.db 由 autotune-tools 管理，记录每次尝试的状态、指标、总结、详情和提交 SHA。
- baseline 只记录最初的 commit SHA，后续合入不修改它。
- best 从用户的本地仓库创建，初始 HEAD 等于 baseline。它始终是同一个 worktree；每个被接受的 Attempt 向它增加一个 squash commit。
- reference 存放参考项目，通过 `autotune-tools` 的多个 `--ref` 参数并行、递归下载；`README.md` 由初始化流程按入选项目填写简介。
- attempts 保存各次计划、验证结果和候选 worktree；完成后保留候选原有提交用于追溯。`BaseCommitSHA` 记录创建时的 best HEAD。


## 优化Attempt

1. 将计划通过 stdin 传给 `new-attempt <workspace>`，创建记录、`plan.md` 和候选 repo。
2. 在候选 repo 中优化，执行正确性与性能验证，准备指标、总结和详情。详情包含验证结果；工具不代替正确性或性能验证。
3. 成功时提交全部待接受的代码修改，保持 repo 干净，执行 `complete-attempt <workspace> <id> --metric '{"latency": 12, "throughput": 56}' --summary '优化结果总结' < detail.md`。`--metric` 必须是名称到有限数值的非空 JSON 对象；summary 不能是空字符串或纯空白。
4. 默认成功合入要求 best 干净且 HEAD 仍等于该 Attempt 的 `BaseCommitSHA`；成功后 squash 合入 best，将状态设为 `completed` 并记录 `SquashCommitSHA`。如果 best 已前进，从新 best 创建 Attempt 并重新验证；不直接合入旧结果。
5. 失败时给同一命令加 `--fail`，仍需指标、总结和 stdin 详情。它只记录 `failed` 和结果，保留 best 与候选 repo 原状，不要求 best 仍为起始 HEAD，也不要求 repo 干净。

两种结果均写入数据库，并生成对应 Attempt 的 `result.json`（状态、指标、总结、详情及 squash SHA；失败时 SHA 为 null）和 `result.md`（Summary、Details 两部分）。如果已有结果文件，成功写入时替换；命令执行出错时回滚数据库并恢复原文件。

创建和合入全程使用 workspace 的 `.autotune.lock` 排他锁。旧版 baseline 目录、best 软链接布局不再支持，应创建新的 workspace。
