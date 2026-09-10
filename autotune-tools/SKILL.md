---
name: autotune-tools
description: 使用 bundled CLI autotune-tools 管理 autotune workspace 时使用。
---

# autotune-tools

使用本 skill bundled 的工具 `scripts/autotune-tools` 管理 autotune workspace，通过子命令的 `--help` 查看参数。`init-workspace` 支持用多个 `--ref` 并行、递归下载参考仓库；`new-attempt <workspace> < plan.md` 从 stdin 读取计划，从干净的 `best` 创建一个 Attempt。

修改 workspace 数据库或 `best` 的工具统一持有 `.autotune.lock` 的 `flock` 排他锁，覆盖读取、修改及失败清理。锁文件持久保留；初始化失败后仅含锁文件的目录可以重试初始化。
