---
name: autokernel
description: >-
  自动调优 CUDA kernel 性能。串联基线构造、按算子选型参考项目、初始化 workspace、
  调优循环。
---

# AutoKernel 自动调优 CUDA kernel 性能

## 流程

1. 参考 [build-tune-baseline](../build-tune-baseline/SKILL.md) 建立验证和性能评测基线，提交需要用于调优的代码与脚本。
2. 初始化 workspace：
   1. 根据待优化代码与用户原始请求，读取 [autokernel-reference](../autokernel-reference/SKILL.md)，选出与该 kernel 相关的参考项目（算子、架构、实现语言匹配；宁缺毋滥，通常 1–4 个）。
   2. 使用 [autotune-tools](../autotune-tools/SKILL.md) 的 `init-workspace`：从用户仓库 HEAD 初始化；stdin 传入非空用户原始请求，保存到 `user_prompt.md`；对每个入选项目传 `--ref <url> <revision> <name>`（`name` 为 `reference/` 下的单层目录名，与项目名一致）。baseline 记录该 SHA，best worktree 作为后续优化起点。
   3. 在 `workspace/reference/README.md` 为每个已下载项目写一条说明：标题用 `name`，下列 GitHub 地址与简介。简介内容取自 [autokernel-reference](../autokernel-reference/SKILL.md) 对应条目（可按本次 kernel 略作裁剪，保留关键词与入口路径）；未入选的项目不要写入。
3. 按 [tune-loop](../tune-loop/SKILL.md) 创建、验证 Attempt；优化时优先查阅 `reference/README.md` 与对应仓库入口文件。将接受的修改 squash 合入 best，并记录对应提交。
