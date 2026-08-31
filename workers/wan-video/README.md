# Wan 2.2 A14B Adult V2 RunPod Worker

第二条自建质量链路，使用 SGLang 原生视频运行时，不依赖 ComfyUI。基础模型为 NVIDIA
`nvidia/Wan2.2-T2V-A14B-Diffusers-FP8`，并强制加载成人 LoRA 的高噪声与低噪声两个权重。
任一 LoRA 缺失都会阻止 Worker 启动，API 不提供关闭适配器的参数。

## 生产规格

- GPU：与 V1 相同的 US-KS-2 `NVIDIA RTX PRO 6000 Blackwell Server Edition` 96GB
  Secure Pod，实测 `$2.09/小时`。L40 实测在加载文本编码器时因 44.39GiB 可用显存 OOM；
  RTX PRO 6000 Serverless 为 `$3.49/小时`，超过 `$3/小时`硬上限，因此只允许按需 Pod。
- 输出：4/5/6/8/10/12/15 秒、16fps、按时长生成 `4N+1` 帧；480p 或 720p；
  16:9、9:16、1:1、4:3、3:4 或 21:9。
- 音频：`cvssp/audioldm2` 根据同一提示词生成同长度环境声/音效，最终 MP4 使用 AAC 音轨；
  它不是口型同步对白模型。Worker 为 Diffusers 0.40 补充 Transformers 5.x 已移除的
  GPT-2 generation cache 更新桥接函数。
- 推理：NVIDIA ModelOpt FP8 双专家与辅助组件常驻 96GB 显存、SGLang 0.5.16、40 steps；
  高/低噪声 CFG 分别 4.0/3.0。LoRA 使用 dynamic 模式分别作用于 `transformer` 与
  `transformer_2`；官方 `speed` 模式与 Torch SDPA，避免 CPU 往返搬运。
- 生命周期：任务触发精确型号 Pod，单任务最长 30 分钟；任务终态或异常后删除 Pod。
  `WAN_AUX_CPU_OFFLOAD=1` 仅用于诊断，不用于 96GB 正式对比。
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
WAN_WORKFLOW_VERSION=wan22-t2v-fp8-resident96-adult-lora-audio-v5
EAGER_LOAD_MODELS=1
```

Railway 的按需 Pod 控制面还需配置：

```dotenv
RUNPOD_WAN_POD_TEMPLATE_ID=<one-shot-template-id>
RUNPOD_WAN_POD_NETWORK_VOLUME_ID=<70GB-US-KS-2-volume-id>
RUNPOD_WAN_POD_CALLBACK_URL=https://your-host.example/generate/api/internal/pod-result
RUNPOD_WAN_POD_GPU_ID=NVIDIA RTX PRO 6000 Blackwell Server Edition
RUNPOD_WAN_POD_DATA_CENTER_ID=US-KS-2
RUNPOD_WAN_POD_FALLBACK_DATA_CENTER_ID=US-NE-1
RUNPOD_WAN_POD_FALLBACK_NETWORK_VOLUME_ID=<70GB-US-NE-1-volume-id>
RUNPOD_WAN_POD_ADDITIONAL_REGION_VOLUMES=[{"data_center_id":"US-NC-2","network_volume_id":"<70GB-US-NC-2-volume-id>"}]
RUNPOD_WAN_POD_MAX_PRICE_PER_HOUR=3.0
RUNPOD_WAN_POD_MAX_RUNTIME_SECONDS=1800
```

创建响应的实际价格超过上限或 GPU 型号不符时，控制面会立刻删除 Pod。Worker 上传成片、
回调任务终态后保持进程存活，Railway 提交数据库结果后在后台删除计费 Pod；30 分钟未终态
也会由成本熔断器强制删除。

内容边界与 V1 相同：仅限合规的虚构成年人内容；拒绝未成年人、非自愿、兽交/乱伦和
真人无授权色情深伪。
