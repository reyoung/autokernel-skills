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

Metrics 表包含:

0. ID: 唯一标识符。自增整数。
1. AttemptID: 对应的 Attempt 的 ID。
2. Name: 指标名称。 Text。
3. Value: 指标值。 Float。

## 工具

init-attempt-db: 初始化一个新的 AutoTune 数据库。
add-attempt: 向 AutoTune 数据库中添加一个新的 Attempt。
add-metric: 向 AutoTune 数据库中添加一个新的 Metric。
fail-attempt: 将指定的 Attempt 标记为失败。
complete-attempt: 将指定的 Attempt 标记为完成。