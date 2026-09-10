---
name: autotune-tools
description: 使用 bundled CLI autotune-tools 管理 autotune workspace 时使用。
---

# autotune-tools

使用本 skill bundled 的工具 `scripts/autotune-tools` 管理 autotune workspace，通过子命令的 `--help` 查看参数。`init-workspace` 初始化，`new-attempt` 从 best 创建候选。`complete-attempt` 必须传入 `--metric` JSON、非空 `--summary`，从 stdin 读取 detail，写数据库并生成结果文件；默认成功并 squash 合入 best，`--fail` 仅记录失败，允许 best 已前进。成功合入要求双方干净且 best 未偏离候选的起始提交。

修改 workspace 数据库、`best` 或结果文件的工具统一持有 `.autotune.lock` 的 `flock` 排他锁，覆盖读取、修改及失败清理，包括 `complete-attempt --fail`。锁文件持久保留；初始化失败后仅含锁文件的目录可以重试初始化。
