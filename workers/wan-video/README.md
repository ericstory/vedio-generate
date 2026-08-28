# Wan 2.2 A14B Adult V2 RunPod Worker

第二条自建质量链路，使用原生 Python/Diffusers，不依赖 ComfyUI。基础模型为官方
`Wan-AI/Wan2.2-T2V-A14B-Diffusers`，并强制加载成人 LoRA 的高噪声与低噪声两个权重。
任一 LoRA 缺失都会阻止 Worker 启动，API 不提供关闭适配器的参数。

## 固定生产规格

- GPU：仅 `NVIDIA RTX PRO 6000 Blackwell Server Edition`，96GB；Secure Pod 上限 $2.50/h，
  Serverless 上限 $3.50/h。
- 输出：5 秒、24fps、121 帧；480p 或 720p；16:9 或 9:16；无音频。
- 推理：BF16 完整双专家、40 steps、CFG 5.0、组件级 CPU offload。
- Serverless：`workersMin=0`、`workersMax=1`、execution timeout 30 分钟。
- 模型卷：至少 150GB，挂载到 `/runpod-volume`。

模型总下载约 127.43GB。挂载卷的临时 Pod 中执行：

```bash
./download_models.sh
```

部署时设置与 LTX Worker 相同的 `VIDEO_UPLOAD_URL` 和 `VIDEO_UPLOAD_TOKEN`。其余变量：

```dotenv
MODEL_ROOT=/runpod-volume/models/Wan2.2-T2V-A14B-Adult-v2
WAN_MODEL_ID=Wan-AI/Wan2.2-T2V-A14B-Diffusers
WAN_MODEL_VERSION=5be7df9619b54f4e2667b2755bc6a756675b5cd7
WAN_ADULT_ADAPTER_ID=lopi999/Wan2.2-I2V_General-NSFW-LoRA
WAN_ADULT_ADAPTER_VERSION=aeef17d7fa51d753ab7d1004ddb4f218a95d756d
WAN_ADULT_ADAPTER_STRENGTH=0.9
WAN_ADULT_TRIGGER=nsfwsks
WAN_INFERENCE_STEPS=40
WAN_GUIDANCE_SCALE=5.0
WAN_WORKFLOW_VERSION=wan22-t2v-adult-lora-v2
EAGER_LOAD_MODELS=1
```

内容边界与 V1 相同：仅限合规的虚构成年人内容；拒绝未成年人、非自愿、兽交/乱伦和
真人无授权色情深伪。
