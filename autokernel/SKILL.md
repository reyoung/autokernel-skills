---
name: autokernel
description: 自动调优cuda kernel性能。
---

# AutoKernel 自动调优cuda kernel性能


## 流程

1. 参考 [build-tune-baseline](../build-tune-baseline/SKILL.md) 建立验证和性能评测基线，提交需要用于调优的代码与脚本。
2. 使用 [autotune-tools](../autotune-tools/SKILL.md) 从用户仓库 HEAD 初始化 workspace；baseline 文件保存该 SHA，best worktree 作为后续优化的起点。
3. 按 [tune-loop](../tune-loop/SKILL.md) 创建、验证 Attempt，将接受的修改 squash 合入 best，并记录对应提交。
