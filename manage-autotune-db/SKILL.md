---
name: manage-autotune-db
description: 管理autotune数据库，创建，添加新数据
---

# AutoTune数据库

AutoTune数据库是一个本地的sqlite文件。一次优化的过程，会新建一个独立的autotune数据库。他包含

1. Attempts 表
2. Metrics 表

## 表结构

Attempts 表包含:

0. ID: 唯一标识符。自增整数。
1. Plan: 优化方法，策略。 Text。
2. Status: `running`, `completed`, `failed`。
3. Summary: Text，总结信息。
4. Details: Text，详细信息。
5. CreatedAt: Timestamp，Attempt 创建的时间。
6. UpdatedAt: Timestamp，Attempt 最近一次更新的时间。
7. BaseCommitSHA: Text，创建 Attempt 时 best 的完整 commit SHA，必填。
8. SquashCommitSHA: Text，Attempt 合入 best 后生成的完整 squash commit SHA。

`running` 表示正在尝试；`completed` 表示已成功 squash 合入 best，必须有 `SquashCommitSHA`；`failed` 表示失败。未完成的记录，其 `SquashCommitSHA` 为 NULL。创建时 `CreatedAt`、`UpdatedAt` 使用当前时间，完成时更新 `UpdatedAt`。

Metrics 表包含:

0. ID: 唯一标识符。自增整数。
1. AttemptID: 对应的 Attempt 的 ID。
2. Name: 指标名称。 Text。
3. Value: 指标值。 Float。

## 写入约定

通过 [autotune-tools](../autotune-tools/SKILL.md) 初始化数据库、创建 Attempt 和记录结果。`new-attempt` 写入 `BaseCommitSHA`。`complete-attempt` 必须传入 `--metric` JSON 和非空白 `--summary`，从 stdin 读取完整 UTF-8 detail，分别写入 Metrics 表及 Attempts 的 Summary、Details，并更新 UpdatedAt。最终指标替换该 Attempt 已有的指标，其他 Attempt 不受影响。

命令默认成功：检查 best HEAD 等于 `BaseCommitSHA`，squash 合入后写入 `completed` 和 `SquashCommitSHA`。传入 `--fail` 时写入 `failed`，`SquashCommitSHA` 为 NULL，保留 best 和候选 repo 原状，允许 best 已前进及 repo 有未提交修改。两种方式均只接受 `running` 记录。

指标、总结、详情与状态在同一事务中提交，并生成 Attempt 的 `result.json` 和 `result.md`。所有修改数据库、best 或结果文件的工具都应持有 workspace 的 `.autotune.lock` 排他锁，并在失败清理完成后释放。旧数据库缺少这两个 SHA 字段时明确报错，本版不自动迁移。
