# Wan 2.2 A14B Adult V2 RunPod Worker

第二条自建质量链路，使用原生 Python/Diffusers，不依赖 ComfyUI。基础模型为官方
`Wan-AI/Wan2.2-T2V-A14B-Diffusers`，并强制加载成人 LoRA 的高噪声与低噪声两个权重。
任一 LoRA 缺失都会阻止 Worker 启动，API 不提供关闭适配器的参数。

## 生产规格

- GPU：RunPod 96GB Pro 池；当前真实验证落在 `NVIDIA RTX PRO 6000 Blackwell Server Edition`。
- 输出：4/5/6/8/10/12/15 秒、16fps、按时长生成 `4N+1` 帧；480p 或 720p；
  16:9、9:16、1:1、4:3、3:4 或 21:9。
- 音频：`cvssp/audioldm2` 根据同一提示词生成同长度环境声/音效，最终 MP4 使用 AAC 音轨；
  它不是口型同步对白模型。
- 推理：BF16 完整双专家、40 steps、CFG 5.0、组件级 CPU offload。
- Serverless：空闲 `workersMin=0`、`workersMax=0`；提交期间最多 1 个 worker；
  execution timeout 120 分钟。
- 模型卷：至少 150GB，挂载到 `/runpod-volume`。

Wan 与 LoRA 总下载约 127.43GB；AudioLDM2 仅保存 safetensors、不保存重复 `.bin` 权重，
额外约 4.6GB。挂载卷的临时 Pod 中执行：

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
WAN_AUDIO_MODEL_ROOT=/runpod-volume/models/Wan2.2-T2V-A14B-Adult-v2/audio/audioldm2
WAN_AUDIO_INFERENCE_STEPS=50
WAN_AUDIO_GUIDANCE_SCALE=3.5
WAN_WORKFLOW_VERSION=wan22-t2v-adult-lora-audio-v3
EAGER_LOAD_MODELS=1
```

内容边界与 V1 相同：仅限合规的虚构成年人内容；拒绝未成年人、非自愿、兽交/乱伦和
真人无授权色情深伪。
