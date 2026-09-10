---
name: build-tune-baseline
description: "与用户确定代码优化 baseline 的指标、正确性判据和测量方式，生成 benchmark/verify 脚本及接口元信息。用户要求构造基线评测脚本时使用。"
---

# 构造 baseline 脚本

## 确定脚本怎么构造

阅读待测代码、现有测试和项目约定，根据用户目标提出方案。只追问尚未明确且会影响脚本构造的选择；代码能回答的问题直接查，已有明确要求直接沿用。

| 决策 | 需要确定的内容 |
| --- | --- |
| 测什么 | 被测入口、输入与工作量 |
| 记录什么指标 | 各 metric 的名称、单位和方向：越大越好或越小越好 |
| 怎样算对 | 由用户选择保持当前行为或对照独立规格，确定参考依据、比较规则和容差或断言 |
| 怎样测量 | 测量边界、重复方式、聚合方式，以及正确性与性能稳定性的判定标准 |
| 覆盖什么情况 | case 的构成与适用范围 |

具体边界情况根据代码按需讨论；实现细节沿用项目惯例。

## 脚本要求

- 包含 verify 和 benchmark 两个方面，分别用于正确性检查和指标测量；可以是独立脚本或同脚本的两个模式，均可单独调用。
- 两者主要执行逻辑、配置和工作量定义一致，核心正确性判据一致；允许独立编排。参考实现独立于候选实现，不能转调候选实现。
- benchmark 独立测量实际完成的工作量，校验每个实际测量输出，校验置于测量边界之外；另行重跑的输出不能替代本次输出。
- 支持多个 metric，分别输出各项结果；实现已约定的正确性与性能稳定性检查。
- verify 和 benchmark 各自至少支持 1,000 个不同的 case，可复用同一套 case。通过不同输入、参数或边界条件形成覆盖；同一 case 的重复运行不计入数量。
- 两者默认运行全量 case，支持参数选择（如 `--case-id 1,2,3`）和列举可用 ID（如 `--list-cases`，列举后退出）。case ID 稳定且唯一，无效选择明确报错；参数命名可沿用项目惯例。
- 检查失败或选中的 case 未执行时返回非零退出码，并定位到指标和 case；不能丢弃失败样本后报告成功。
- 构造 CUDA kernel baseline 时，读取并应用 [cuda-baseline-notes](../cuda-baseline-notes/SKILL.md) 正文。

## 生成与交付

用户确认方案后，按项目惯例生成并检查脚本及必要配置，依据 [schema](scripts/baseline_meta.schema.json) 生成 `.autokernel/baseline_meta.json`，使用 [检查脚本](scripts/validate_baseline_meta.py) 校验后交付。元信息格式参考 [示例](scripts/baseline_meta.example.json)。

`metrics.benchmark` 须覆盖用户声明的优化目标（名称、单位、direction）；后续 `init-workspace` 从中选出本次监控的子集并写入 workspace 的 `target-metric.json`。verify 指标用于正确性，不进入帕累托监控集合，除非用户明确要求。
