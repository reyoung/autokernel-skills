---
name: autokernel-reference
description: >-
  CUDA kernel 调优的参考项目索引。写 GEMM、attention、MoE、norm/softmax kernel，或选择
  CUDA C++ / CuTe / CuteDSL / Triton / TileLang 实现路线时，用它定位可借鉴的开源实现。
---

# AutoKernel 参考项目

按「要写的算子 + 目标架构 + 实现语言」检索下面的项目。在 AutoKernel 流程中由 `init-workspace --ref` 下载到 workspace 的 `reference/`（revision 取上游 `main` 或 `master`），不要另克隆到别处。各条目列出的目录为该仓库内的相对路径。

## 算子库（生产级参考实现）

### DeepGEMM
https://github.com/deepseek-ai/DeepGEMM

DeepSeek 的 FP8/FP4/BF16 GEMM 与 MoE 算子库，手写 CUDA C++ + CuTe，只借用 CUTLASS 的 barrier/arch，不用其 GEMM 模板。覆盖稠密 GEMM、M-grouped contiguous/masked（MoE decode，配合 CUDA Graph）、K-grouped（MoE 反向）、Mega MoE 超核（dispatch + GEMM + SwiGLU + combine 融合，NVLink overlap）、MQA logits indexer。关键技术：TMA 多阶段流水与 multicast、WGMMA warpgroup 分工、ClusterTransactionBarrier、SMEM swizzle、ring pipeline、persistent scheduler、PDL；SM100 走 tcgen05 与 UE8M0 packed scale。DeepJIT 运行时编译 + heuristics 选 block size，安装期不编译 CUDA。入口：`deep_gemm/include/deep_gemm/impls/`、`common/tma_copy.cuh`、`scheduler/gemm.cuh`。

### FlashMLA
https://github.com/deepseek-ai/FlashMLA

DeepSeek V3/V4 的 Multi-head Latent Attention 内核库，CUDA C++ + CuTe + CUTLASS cluster launch，按稀疏/稠密 × prefill/decode 分成四条主线，编译期 `.cu` 实例化而非 JIT。SM90 decode 的范式：split-KV 主 kernel + combine kernel（PDL grid 同步合并 LSE）、FP8 KV tile 反量化、2-CTA cluster crossover（分布式共享内存 `st.async` 交换）、persistent 变体。SM100 用 TMEM/UTCMMA 做 Copy/MMA/Softmax 三级流水的稀疏 prefill，并有 norm+RoPE+attn+cast 融合超核。长上下文 decode、稀疏 top-k attention、paged FP8/FP4 KV 布局的首选参考。入口：`csrc/kernels/sm90/decode/`、`csrc/kerutils/`、`docs/` 下的 deep-dive 文档。

### FlashInfer
https://github.com/flashinfer-ai/flashinfer

LLM 推理 serving 的生产 kernel 库，被 SGLang、vLLM、TensorRT-LLM 集成。三层架构值得照抄：`include/flashinfer/` 存框架无关的 header-only CUDA 模板，`csrc/` 做 launcher 与 FFI 绑定，`flashinfer/jit/` 按 dtype/head_dim/arch 做 Jinja 特化并 JIT 缓存。算子覆盖 decode/prefill/append、paged 与 ragged KV、GQA/MQA、MLA、cascade 共享前缀、POD 混合 batch、FP8/NVFP4 attention、MoE 路由与 fused GEMM、免排序 Top-K/Top-P 采样、RoPE/RMSNorm。架构跨 SM75 到 Blackwell，多后端 dispatch（fa2/fa3/cudnn/cutlass/trtllm）。学其 AttentionVariant 钩子、`paged_kv_t` + fastdiv 索引、LSE cascade merge、plan-run workspace 模式。

### FlashAttention
https://github.com/Dao-AILab/flash-attention

IO-aware 精确注意力的权威实现：分块 QK^T·V + online softmax，不 materialize 完整 attention matrix。一个仓库并存三代，可按目标架构选参考：FA2 在 `csrc/flash_attn/`（SM80，CUDA + CuTe，`flash_fwd_kernel.h` 是最易读的主循环）；FA3 在 `hopper/`（SM90，CUTLASS collective 风格的 TMA + WGMMA warp-specialized mainloop）；FA4 在 `flash_attn/cute/`（CuteDSL Python，覆盖 SM90/SM100/SM120，SM100 用 tcgen05 + TMEM + CLC scheduler）。关键词：kBlockM/kBlockN tiling、multi-stage pipeline、named barrier、PackGQA、paged KV、split-KV combine、FP8 与 V 转置、block sparsity、causal/sliding-window/softcap mask。FA4 复用 QuACK 的 `copy_utils`、`sm90_utils`。

### QuACK
https://github.com/Dao-AILab/quack

Dao-AILab 的纯 CuteDSL（Python）kernel 库，是写 CuteDSL 最接近生产代码的范本，比 CUTLASS examples 更完整。两类算子：memory-bound 的 RMSNorm/LayerNorm、Softmax、CrossEntropy（含反向，`reduce.py` 是 speed-of-light 归约方法论的落点）；compute-bound 的 GEMM 分架构实现 `gemm_sm90.py`（TMA + WGMMA persistent）、`gemm_sm100.py`（tcgen05 + TMEM + CLC）、`gemm_sm120.py`，带 rotary、quantize-out、head-RMSNorm 等可组合 epilogue。基础设施可直接借用：`spec/`（TMA/SMEM/MMA/TMEM 抽象）、`copy_utils.py`、`pipeline.py` 与 `pipeline_checks.py`、`tile_scheduler.py`、`autotuner.py` 与 JIT cache。需 CUDA 12.9+。

### CUTLASS
https://github.com/NVIDIA/cutlass

NVIDIA 官方 GEMM/线性代数模板库，也是 CuTe 与 CuteDSL 的上游，FlashAttention-4、QuACK、FlashInfer 都建在其上。三层接口：CUTLASS 3.x C++ collective（`include/cutlass/gemm/collective/`，mainloop + epilogue 组合）、CuTe C++ 的 layout/tensor 代数（`include/cute/`）、CuteDSL Python（`examples/python/CuTeDSL/`）。技术栈覆盖分层 tiling（thread/warp/warpgroup/CTA/cluster）、TMA async copy 与 multicast、Hopper WGMMA、Blackwell tcgen05 + TMEM、persistent/CLC tile scheduler、warp specialization、blockscaled NVFP4/MXFP8、mbarrier pipeline。写 Tensor Core kernel 时先读 CuteDSL 的 TMA/MMA/softmax primitives 教程与 `hopper/dense_gemm.py`。注意需按 `90a`/`100a` 编译，SM100 与 SM120 二进制不通用。

## Kernel DSL

### TileLang
https://github.com/tile-ai/tilelang

基于 TVM/TIRX 的 Python GPU DSL，`@tilelang.jit` 编译到 CUDA/HIP/Metal/CPU。编程模型是显式分块 + 显式存储层级，写起来像用 Python 表达 CUTLASS 结构：`T.Kernel` 定网格与线程数，`T.alloc_shared`/`T.alloc_fragment` 分配 SMEM 与寄存器累加器，`T.copy`/`T.tma_copy` 搬运，`T.Pipelined(num_stages=N)` 开软件流水，`T.gemm` 落到 MMA/WGMMA/MFMA，`T.Parallel` 写 epilogue；layout 推断、流水插入、warp specialization 由编译器负责，也可下探到 PTX 级。适合 FlashAttention/GQA/MLA、FP8 与块缩放 GEMM、MoE 等结构清晰的融合算子，且需要跨 NVIDIA/AMD 可移植时。入口示例：`examples/gemm/`、`examples/flash_attention/`、`examples/deepseek_mla/`，自带 autotuner。

### Triton
https://github.com/triton-lang/triton

Python GPU DSL + MLIR 编译器，链路为 TTIR → TTGIR → LLVM → PTX/HSACO，官方支持 NVIDIA CC 8.0+ 与 AMD ROCm 6.2+。SPMD 模型：一个 program 负责一个输出 tile，用 `tl.program_id` 取坐标、`tl.arange` 加 stride 算术构造 blocked pointer、`tl.load/store` 带 mask 访存、`tl.dot` 做矩阵乘，tile 尺寸走 `tl.constexpr`，配合 `@triton.autotune` 与 `num_warps`/`num_stages` 调优；共享内存布局与流水线由编译器推断。Hopper+ 可用 `TensorDescriptor` 走 TMA。抽象层封顶时切到 Gluon（`triton.experimental.gluon`），显式操作 layout、TMA、mbarrier、WGMMA、warp specialization。入口：`python/tutorials/03-matrix-multiplication.py`、`06-fused-attention.py`、`tutorials/gluon/`。

## 教学与手写 PTX

### LeetCUDA
https://github.com/xlite-dev/LeetCUDA

200+ 个带 PyTorch 绑定的独立 CUDA kernel，按 Easy 到 Hard++ 分级，单文件、重注释、附 ASCII 线程布局图，适合在动手前补齐底层写法。主线是同一算子的递进阶梯：naive → `cp.async` 多 stage → `ldmatrix` + `mma.sync m16n8k16` → SMEM/block swizzle 消 bank conflict → CuTe → Hopper WGMMA + TMA + warp specialization。旗舰目录 `kernels/hgemm/`（可达 cuBLAS 98–100%）、`kernels/flash-attn/`（FA2 split-Q、split-KV、QKV SMEM 复用、fine-grained tiling）、`kernels/swizzle/`，`kernels/interview/notes-v2.cu` 是对齐 cuBLAS/cuDNN/FA2 的统一 benchmark。另含 Triton 版对照实现。用它学 tiling 参数化与 PTX 宏，不学工程化。

## 选型提示

- 手写 CUDA C++ / PTX：LeetCUDA 打底，DeepGEMM、FlashMLA、FlashAttention（FA2/FA3）是生产范式。
- CuTe / CuteDSL：CUTLASS 是基础设施，QuACK、FlashAttention（FA4）是可直接对照的生产代码。
- DSL 快速迭代：通用融合算子用 Triton；显式 tile GEMM/attention 流水、TMA/warp specialization、跨厂商移植用 TileLang。
- Serving 侧工程结构（paged KV、JIT 特化、plan-run、多后端 dispatch）：FlashInfer。
