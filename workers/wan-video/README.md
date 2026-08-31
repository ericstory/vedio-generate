# Wan 2.2 A14B Adult V2 RunPod Worker

第二条自建质量链路，使用 SGLang 原生视频运行时，不依赖 ComfyUI。基础模型为 NVIDIA
`nvidia/Wan2.2-T2V-A14B-Diffusers-FP8`，并强制加载成人 LoRA 的高噪声与低噪声两个权重。
任一 LoRA 缺失都会阻止 Worker 启动，API 不提供关闭适配器的参数。

## 生产规格

- GPU：与 V1 相同的 US-KS-2 `NVIDIA L40` 48GB；Serverless Flex 官方价约
  `$1.908/小时`，低于 `$3/小时`硬上限，创建前仍需复查实时价格和库存。Queue-based
  Serverless 没有 MIG48 pool，因此不能把 Pod 的 MIG48 选择直接用于 Endpoint。
- 输出：4/5/6/8/10/12/15 秒、16fps、按时长生成 `4N+1` 帧；480p 或 720p；
  16:9、9:16、1:1、4:3、3:4 或 21:9。
- 音频：`cvssp/audioldm2` 根据同一提示词生成同长度环境声/音效，最终 MP4 使用 AAC 音轨；
  它不是口型同步对白模型。Worker 为 Diffusers 0.40 补充 Transformers 5.x 已移除的
  GPT-2 generation cache 更新桥接函数。
- 推理：NVIDIA ModelOpt FP8 双专家常驻显存、SGLang 0.5.16、40 steps；高/低噪声 CFG
  分别 4.0/3.0。LoRA 使用 dynamic 模式分别作用于 `transformer` 与 `transformer_2`；只把
  T5 和 VAE 辅助组件放到 CPU，避免旧版约 126GB BF16 双专家反复搬运。
- Serverless：空闲 `workersMin=0`、`workersMax=0`；提交期间最多 1 个 worker；
  首轮 execution timeout 30 分钟，验证后最长不超过 45 分钟。
- 模型卷：FP8 + 双 LoRA + AudioLDM2 约 50.73GB；建议独立 70GB 卷，挂载到 `/runpod-volume`。

AudioLDM2 仅保存 safetensors、不保存重复 `.bin` 权重。挂载卷的临时 Pod 中执行：

```bash
./download_models.sh
```

部署时设置与 LTX Worker 相同的 `VIDEO_UPLOAD_URL` 和 `VIDEO_UPLOAD_TOKEN`。其余变量：

```dotenv
MODEL_ROOT=/runpod-volume/models/Wan2.2-T2V-A14B-Adult-FP8-v4
WAN_MODEL_ID=nvidia/Wan2.2-T2V-A14B-Diffusers-FP8
WAN_MODEL_VERSION=2c5a06469cd2255816eb2e46b8e11600ed435d52
WAN_ADULT_ADAPTER_ID=lopi999/Wan2.2-I2V_General-NSFW-LoRA
WAN_ADULT_ADAPTER_VERSION=aeef17d7fa51d753ab7d1004ddb4f218a95d756d
WAN_ADULT_ADAPTER_STRENGTH=0.9
WAN_ADULT_TRIGGER=nsfwsks
WAN_INFERENCE_STEPS=40
WAN_GUIDANCE_SCALE=4.0
WAN_GUIDANCE_SCALE_2=3.0
WAN_AUDIO_MODEL_ROOT=/runpod-volume/models/Wan2.2-T2V-A14B-Adult-FP8-v4/audio/audioldm2
WAN_AUDIO_INFERENCE_STEPS=50
WAN_AUDIO_GUIDANCE_SCALE=3.5
WAN_WORKFLOW_VERSION=wan22-t2v-fp8-adult-lora-audio-v4
EAGER_LOAD_MODELS=1
```

内容边界与 V1 相同：仅限合规的虚构成年人内容；拒绝未成年人、非自愿、兽交/乱伦和
真人无授权色情深伪。
