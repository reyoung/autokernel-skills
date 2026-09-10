---
name: tune-loop
description: 执行 CUDA kernel 调优循环时使用，生成候选实现、验证正确性、评测性能并记录每次尝试。
---

# 调优循环


## 概况

循环是调优的主要过程。他根据用户指定的baseline和优化目标，进行一次一次的尝试(Attempt)。直到用户的优化目标达成，或者调优循环次数终止。用户在调优开始的时候，需要指定一个Workspace目录。调优的过程，均记录在这个workspace目录中。


## 优化目标

用户可以指定多个优化目标。每一次尝试，可以是对单个目标的优化，也可以是对多个目标的综合优化。但优化过程是帕累托最优的。也就是每一次尝试不要降低其他目标的性能。


## Workspace 目录

Workspace目录中，包含
```
workspace
  `-- autotune.db
  `-- baseline(git worktree of baseline commit)
  `-- best(link to best attempt repo or baseline)
  `-- reference
  `-- attempts
      `-- attempt-00001
           `-- plan.md
           `-- result.json (metrics and correctness)
           `-- result.md (summary and detail of this attempt)
           `-- repo(git worktree of attempt, fork from best, could be dirty)
      `-- attempt-00002
      ...
```


其中

- autotune.db 参考 manage-autotune-db skill，记录每一次尝试的状态信息。
- baseline 是优化开始时对应的baseline commit。使用 git worktree 方式来生成目录。
- reference 是当前优化可以参考的开源项目。参考 init-autotune-reference skill 来初始化
- best 是当前优化的最佳实现。它是一个软链接，指向当前最佳实现的repo目录，或者baseline目录。
- attempts 是每一次尝试记录。只增不减。
  - plan.md 参考技能 generate-autotune-plan skill 来创建。 他是这次Attempt 的优化计划与策略。
  - result.json/result.md 是这次优化的结果。 参考 record-autotune-result skill 来记录。
  - repo 是这次尝试的候选实现。他从当前最佳实现来派生。


## 优化Attempt

参考 autotune-attempt skill 来执行一次优化尝试。