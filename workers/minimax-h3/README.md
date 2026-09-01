# MiniMax H3 + PinkCherry 主线 RunPod Worker

NSFW 视频主线链路。使用 SGLang Diffusion 原生运行时（`DiffGenerator` Python API），
**不依赖 ComfyUI**、不起 HTTP server、不跑第二个音频模型。

选型依据见 [`docs/model-gpu-selection-2026-09.md`](../../docs/model-gpu-selection-2026-09.md)。
一句话：MiniMax H3 是 Artificial Analysis 盲评里开源第一的视频模型（T2V Elo 1301 无音频 /
1227 有音频），原生同步音视频，而 PinkCherry 的作者已经把 V1 那套 NSFW 微调整体迁到了 H3。

## 组成

| 层 | 来源 | 说明 |
| --- | --- | --- |
| 基座 | `MiniMaxAI/MiniMax-H3` FL2VA 分区 | 文本编码器（Qwen3-VL 32B）、video VAE、audio VAE、tokenizer/processor/config。FL2VA 同时服务 `t2va` 与首尾帧条件 |
| NSFW | `SexGod1979/PinkCherry_MiniMax-H3` beta-0.6 | **完整微调 DiT，不是 LoRA**；经 SGLang 单文件 `transformer_weights_path` 覆盖官方 transformer |
| 加速 | `lightx2v/Minimax-h3-Turbo` 8 步蒸馏 LoRA | Apache-2.0，`key_format=minimax-h3-diffusers` 的 plain 导出 |

PinkCherry 可用性是**验证过的**，不是假设：读取 safetensors 头部确认它有 535 个张量，
名称与形状和官方 FL2VA transformer 完全一致（`blocks.N.attn.qkv_proj.weight [21504, 5376]`、
`blocks.N.adaln_proj.linear.weight [96768, 2688]`、`audio_patch_proj.weight [5376, 32]`），
且内嵌 config 与 MiniMax H3 transformer 配置逐项吻合。

⚠️ 同仓库的 `int8_convrot` / `pruned_int8_convrot` 变体**不能用**：它们带 ComfyUI 的
`comfy_quant` U8 张量和裁剪过的 AdaLN 路径（`adaln_proj.linear.weight [96768, 8]` +
`adaln_t_table`），SGLang 的 loader 不实现这套。只用 bf16 导出，量化交给 SGLang 在线做。

## 生产规格

- **GPU**：`NVIDIA RTX PRO 6000 Blackwell Server Edition` 96GB Secure Pod，`$2.09/小时`；
  同池的 Workstation Edition（`$1.89/小时`）作为同架构兄弟卡兜底。SM 12.0 是硬需求：
  在线 FP8 需要 SM ≥ 8.9，本镜像的 SageAttention kernel 只编译了 SM 12.0。
- **输出**：4–15 秒、**固定 24fps**、短边 768（唯一被 MiniMax/SGLang 实测过的配置）；
  21:9 / 16:9 / 4:3 / 1:1 / 3:4 / 9:16。H3 按短边和宽高比解析画布，再套 768×1344 面积上限。
- **音频**：H3 一次前向同时产出视频和 **32kHz 立体声**，直接 mux 成 H.264 + AAC。
  没有 AudioLDM2、没有第二次推理、没有 mux 步骤。`generate_audio=false` 时用
  ffmpeg `-an` 去掉音轨（仅复制流，不重编码）。
- **推理**：SGLang 0.5.18、在线 FP8 量化、SageAttention、8 步蒸馏 LoRA、`flow_shift=12.0`、
  `audio_flow_shift=3.0`。H3 只有 CFG 蒸馏的单正分支——**没有 negative prompt、
  没有 guidance scale**，传了会被拒。
  ⚠️ 步数是 **9 不是 8**：所有官方 turbo 配方都比 LoRA 名字多要一步（4 步档用 5、
  平衡档用 9），因为 flow schedule 的 N 段去噪需要 N+1 个 sigma。
- **生命周期**：任务触发精确型号 Pod，单任务最长 30 分钟，终态或异常后删除 Pod。
- **模型卷**：默认约 145.4GB，建议 **170GB** 独立卷挂到 `/runpod-volume`。
  开 `INCLUDE_STOCK_TRANSFORMER=1` 做原版 vs PinkCherry 对照需约 211.7GB / 220GB 卷。

## 下载模型

在挂了卷的临时 Pod 里执行：

```bash
./download_models.sh
```

默认**不下载**官方那 66.28GB 的 transformer 分片（PinkCherry 整体替换掉了）。
需要做质量 A/B 时：`INCLUDE_STOCK_TRANSFORMER=1 ./download_models.sh`。
需要 4 步激进档：`INCLUDE_FAST_TURBO_LORA=1`。

## Worker 环境变量

部署时设置与其它 Worker 相同的 `VIDEO_UPLOAD_URL` 和 `VIDEO_UPLOAD_TOKEN`。其余：

```dotenv
MODEL_ROOT=/runpod-volume/models/MiniMax-H3-PinkCherry
H3_MODEL_ID=MiniMaxAI/MiniMax-H3
H3_MODEL_VERSION=42ed227ee7df40d41602854ae760620d6eb651fe
H3_MODEL_VARIANT=fl2va
H3_NSFW_MODEL_ID=SexGod1979/PinkCherry_MiniMax-H3
H3_NSFW_MODEL_VERSION=bf2fef11d0e55e957f4af997e3beade3362f44b3
H3_NSFW_TRANSFORMER_ENABLED=1
H3_TURBO_LORA_ENABLED=1
H3_TURBO_LORA_STRENGTH=1.0
H3_INFERENCE_STEPS=9
H3_FLOW_SHIFT=12.0
H3_AUDIO_FLOW_SHIFT=3.0
H3_QUANTIZATION=fp8
H3_ATTENTION_BACKEND=sage_attn
H3_QUALITY=lossless
H3_TEXT_ENCODER_CPU_OFFLOAD=1
H3_WORKFLOW_VERSION=h3-fl2va-pinkcherry-turbo8-v1
EAGER_LOAD_MODELS=1
```

改档位只改模板 env，不用重建镜像：

| 想做的事 | 改什么 |
| --- | --- |
| 回到原版 H3（质量 A/B 的对照侧） | `H3_NSFW_TRANSFORMER_ENABLED=0` |
| 关掉蒸馏、跑 50 步原生档 | `H3_TURBO_LORA_ENABLED=0` + `H3_INFERENCE_STEPS=50` |
| 换 4 步激进档 | `H3_TURBO_LORA_PATH=<4step 文件>` + `H3_INFERENCE_STEPS=5` |
| 在线 FP8 出问题时退回 BF16 | `H3_QUANTIZATION=`（留空） |
| 开 Cache-DiT 加速档（1.40×，SSIM 0.931） | `H3_QUALITY=high` |
| LoRA 合并模式出问题 | `H3_LORA_MERGE_MODE=merge` 或 `dynamic` |

## 首轮上机前必须知道的未验证项

这条链路的代码路径全部对着 SGLang v0.5.18 的真实源码写的，但**没有任何一项在
RTX PRO 6000 上实跑过**。第一次付费运行要确认：

1. **峰值显存**。第三方数据：8×B300 FP8 每卡 51.9GB；4×H100 TP4 每卡 49.8GB；
   2×RTX 5090 带 layerwise offload 每卡 26.3GiB。单卡 96GB 常驻应该够，但没实测。
2. **单次耗时**。第三方：RTX 5090 4 步约 19 秒；RTX 4090 24GB 带 offload、
   kitchen_int8 + sage_attn、50 步约 174.9 秒。RTX PRO 6000 无数据。
3. **在线 FP8 之后再 merge LoRA 是否正确**。Wan 上踩过 merge 模式对预量化
   modelopt-FP8 有维度 bug；H3 是 BF16 底座 + 在线量化，属于另一条代码路径，
   但顺序风险仍在。出问题就 `H3_QUANTIZATION=` 退回 BF16。
4. **145GB 的卷加载有多慢**。Wan 在约 50GB 时实测 `model_load` 在 90s–805s 之间抽奖，
   H3 体量接近 3 倍。`model_load_seconds` 已单独计时上报。
5. **30 分钟 Pod 上限的预估守卫还没标定**。`H3_SECONDS_PER_MPIXEL_STEP` 默认 `0`
   即关闭——没有实测数据之前不假装能预测。第一次成功运行后按实测填进去，
   守卫就会在计费开始前拒掉必然超时的组合（Wan 就是栽在这里）。

## 内容边界

沿用既有规定：仅限合规的虚构成年人内容，拒绝未成年人、非自愿、兽交/乱伦和真人
无授权色情深伪。`worker_config.validate_prompt` 在任何 GPU 时间产生之前执行。
