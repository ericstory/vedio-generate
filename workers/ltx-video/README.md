# PinkCherry LTX 2.3 v1.8 RunPod Worker

独立于 Seedance 的自建云 GPU 链路。Worker 使用 Lightricks 官方 Python pipeline，生成结果上传到 R2/S3；模型权重不进入镜像和 Git。

## 模型准备

在 RunPod Network Volume 的 `/runpod-volume/models/PinkCherry-LTX-2.3-v1.8` 准备：

- `v1.8/PinkCherry_FineTune_bf16_v1_8_LTX23.safetensors`
- `ltx-2.3-22b-distilled-lora-384-1.1.safetensors`
- `ltx-2.3-spatial-upscaler-x2-1.1.safetensors`
- `gemma-3-12b/`（`google/gemma-3-12b-it-qat-q4_0-unquantized`）

合计约 79.2GB，因此 Volume 至少使用 100GB。`models.lock.json` 固定三个 Hugging Face
仓库 revision、关键文件大小和 SHA256；脚本只下载 v1.8 BF16，不会把仓库中的旧版本和
示例视频一起下载。

接受 Hugging Face 上的模型条款并执行 `hf auth login` 后，可在挂载该 Volume 的临时 Pod 中运行：

```bash
./download_models.sh
```

脚本会自动选择临时 Pod 的 `/workspace` 或 Serverless 的 `/runpod-volume`，并同时下载
PinkCherry、LTX 和 Gemma 三个仓库。`DOWNLOAD_MAX_WORKERS` 默认为 8；中断后重跑会续传，
不会重新下载已经完成的文件。100GB Network Volume 要避免存入 Docker 层、系统包缓存或
其他模型，以给约 79.2GB 的权重和下载元数据保留余量。

最低规格 CPU Pod 内存较小时，使用 `DOWNLOAD_PARALLEL_REPOS=0 DOWNLOAD_MAX_WORKERS=2`
按仓库顺序下载，避免多个 Xet 进程同时占用内存；单个仓库内部仍保留并发和断点续传。

首轮使用 BF16 文件配合 `LTX_QUANTIZATION=auto`，这比直接启用第三方 FP8-scaled
checkpoint 的兼容性风险低。Ada/Hopper/Blackwell 自动使用 `fp8-cast`；A100 等 Ampere
卡自动保留 BF16，因此同一 Serverless Endpoint 可以配置多 GPU 回退。如显存不足再启用
`LTX_OFFLOAD=cpu`，但会明显降低速度。

## GPU 选择范围

选卡策略固定在 `gpu_policy.json`：至少 48GB VRAM，Secure Cloud 参考价上限约
`$2.10/小时`，暂不使用 H100/H200。优先使用支持 FP8 的 L40/L40S、RTX 6000 Ada 和
RTX PRO 6000 系列；A100 80GB 作为 BF16 回退。A40 和 RTX A6000 虽有 48GB，但不支持
本链路的 FP8 路径，BF16 又过于贴近显存上限，因此不加入自动调度列表。

## 许可证与内容边界

PinkCherry 模型页标记 Apache-2.0，但它是 LTX 2.3 衍生权重，基础权重仍受 LTX-2
Community License 约束。当前服务仅用于密码保护的私人研究。Worker 允许合规的成年人
内容，同时拒绝未成年人性内容、非自愿行为、兽交/乱伦和真人无授权色情深伪。

## Worker Variables

```dotenv
# Preferred for the private Railway deployment: authenticated upload into its
# existing persistent volume. Use the same long random token on both services.
VIDEO_UPLOAD_URL=https://video-generator-production-8c1e.up.railway.app/generate/api/internal/video-upload
VIDEO_UPLOAD_TOKEN=
VIDEO_UPLOAD_TIMEOUT_SECONDS=300

# Optional generic R2/S3 fallback when VIDEO_UPLOAD_URL is not set.
S3_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
S3_REGION=auto
S3_BUCKET=papa-video
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
S3_PUBLIC_BASE_URL=https://media.example.com
LTX_QUANTIZATION=auto
LTX_OFFLOAD=none
LTX_INFERENCE_STEPS=20
LTX_DISTILLED_LORA_STRENGTH=0.6
EAGER_LOAD_MODELS=1
SELF_HOSTED_MODEL_ID=SexGod1979/PinkCherry_NSFW_LTX23
SELF_HOSTED_MODEL_VERSION=PinkCherry_FineTune_bf16_v1_8_LTX23
SELF_HOSTED_WORKFLOW_VERSION=pinkcherry-native-two-stage-v1
```

The Railway upload endpoint stores MP4 files in `VIDEO_OUTPUT_DIR` and serves them only to an
authenticated admin session. The upload itself uses a separate bearer token and accepts MP4 files
up to 250MB, so RunPod credentials never need to be exposed to the browser.

构建并推送 `linux/amd64` 镜像后，在 RunPod 创建 Queue-based Serverless Endpoint，挂载上述 Network Volume。建议 execution timeout 30 分钟、job TTL 2 小时、min workers 为 0、max workers 先设为 1、idle timeout 为 5 秒。

仓库的 `.github/workflows/ltx-worker.yml` 会把镜像发布到 `ghcr.io/<owner>/papa-ltx-video`。RunPod 使用精确 SHA tag，不建议生产 Endpoint 跟随 `latest`。

镜像默认使用 CUDA 13.2，因为锁定的 LTX commit 的 `natten` extra 固定为
`torch 2.13.0 + cu132`。创建 Pod/Endpoint 时应要求 `min CUDA version=13.2`；如果候选
GPU 池不满足该版本，需要先换用经 GPU 验证的早期 LTX commit，而不是静默降低驱动要求。

## 付费边界

代码、测试、镜像构建和镜像推送都应先完成。下面任一操作都会开始产生 RunPod 费用，
执行前必须再次确认：

- 创建 Network Volume（即使没有 Pod 也持续计费）
- 创建 CPU/GPU Pod
- 创建并调用会启动 worker 的 Serverless Endpoint

创建新区域的 Network Volume 前，应先确认该区域至少有 `gpu_policy.json` 中一种候选卡，
或有可用于预下载模型的 CPU 容量。正式 Serverless Endpoint 使用多个 GPU 类型按顺序回退，
不绑定单一卡型。
