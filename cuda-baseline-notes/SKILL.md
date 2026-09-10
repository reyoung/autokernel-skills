---
name: cuda-baseline-notes
description: >-
  构造 CUDA kernel baseline 时使用，补充 build-tune-baseline 的计时、正确性与脚本耗时要求。
---

# CUDA baseline

通用要求见 [build-tune-baseline](../build-tune-baseline/SKILL.md)。

## 耗时预算

构造脚本前确定完整脚本的墙钟时间预算：从启动到结果输出，包含初始化、编译、数据准备、warmup、backlog、测量和校验，仅排除资源申请排队。

例如：每次 kernel 调用耗时 1 ms，1,000 个 case 各运行 10 次，可约定脚本在 30 s 内完成。实际预算按任务确定。

## 按 chunk 执行

将 case 分成若干 chunk，逐块执行以下步骤。显存不足时缩小 chunk；单个 case 仍无法容纳时，报告所需调整并停止。

1. **准备**：在 GPU 上生成本 chunk 的 reference，复用至校验完成并保持不被候选实现覆盖。提前准备输入、各次调用的独立输出存储，以及每个 case 独立的一对计时 CUDA event。输出保留至校验完成；调用会覆盖旧输出时，改用独立缓冲区，无法保留则调整调用安排后再测量。完成 warmup，恢复被其修改的输入。
2. **Backlog**：在测量所用 stream 上先提交一次不计时的矩阵乘法。实测其 GPU 耗时须覆盖本 chunk 的 CPU 提交时间；不足则调整矩阵规模或 chunk 大小后重测。
3. **计时**：在同一 stream 上，逐 case 记录 start event、提交恰好 `n` 次被测调用、记录 end event。准备、校验和日志置于计时区间外，CPU 等待集中到 chunk 收尾。
4. **收尾**：提交完本 chunk 后，等待相关 GPU 工作完成，统一读取 event 耗时、校验每次实际测量输出并汇总，再回收数据。

## 完成标准

全部选定 case 的实际测量输出均通过校验。分别报告被测 GPU 耗时与完整脚本耗时，分别判定正确性和耗时预算是否达标。
