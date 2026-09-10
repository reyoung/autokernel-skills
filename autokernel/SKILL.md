---
name: autokernel
description: >-
  自动调优 CUDA kernel 性能。串联基线构造、按算子选型参考项目、初始化 workspace、
  调优循环。
---

# AutoKernel 自动调优 CUDA kernel 性能

## 流程

1. 参考 [build-tune-baseline](../build-tune-baseline/SKILL.md) 建立验证和性能评测基线（含用户优化目标对应的 benchmark 指标），提交代码与脚本；用其检查脚本校验 `.autokernel/baseline_meta.json`。
2. 在已提交的 baseline 代码上运行 benchmark，记下本次要监控的指标实测值。
3. 初始化 workspace：
   1. 根据待优化代码与用户请求，读取 [autokernel-reference](../autokernel-reference/SKILL.md)，选出相关参考项目（通常 1–4 个）。`--ref` 的 revision 用上游默认主线（`main` 或 `master`）。
   2. 使用 [autotune-tools](../autotune-tools/SKILL.md) 的 `init-workspace`：传入 `--baseline-meta`（仓库内 meta 路径）、每个监控指标的 `--metric NAME=VALUE`（NAME ⊆ meta.`metrics.benchmark`，VALUE 为步骤 2 实测值）、可选 `--max-attempts`；stdin 写入用户原始请求；对入选项目传 `--ref <url> <main|master> <name>`。
   3. 在 `workspace/reference/README.md` 为每个已下载项目写说明（标题用 `name`，内容取自 [autokernel-reference](../autokernel-reference/SKILL.md) 对应条目，可按本次 kernel 裁剪）。
4. 按 [tune-loop](../tune-loop/SKILL.md) 调优：每次写 Attempt plan 前先 `get-context`；用 workspace 内 `baseline_meta.json` 做 verify/benchmark，用 `target-metric.json` 判定是否继续；优化时查阅 `reference/README.md` 与对应仓库入口文件。
