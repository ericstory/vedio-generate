# RunPod 双链路交接记录（2026-08-31 更新）

## 状态：同 GPU 对照测试已完成

V1（PinkCherry LTX 2.3）与 V2（Wan 2.2 A14B FP8 + 成人 LoRA + AudioLDM2）已在同一
`NVIDIA RTX PRO 6000 Blackwell Server Edition`（96GB，US-KS-2，Secure `$2.09/h`）、
同提示词/参数/seed 下各自完成端到端运行，指标与视频对照见
[`ab-wan-ltx-rtx-pro-6000.md`](ab-wan-ltx-rtx-pro-6000.md)。终态 Pod 数为 0。

## 本轮变更

- Railway 已部署 main（部署 `f134cc56`），CUDA floor 修复上线并被 NC2 真实建 Pod 验证。
- Wan 镜像两个音频段缺陷已修复并推送：
  - `d341704` — 镜像安装 accelerate（AudioLDM2 CPU offload 硬依赖）
  - `ed4470f` — AudioLDM2 GPT2 rollout 重绑为公开 API 前向（transformers 5 兼容）
- Wan 模板 `wjxhc0dtid` 镜像已指向
  `ghcr.io/ericstory/papa-wan-video:ed4470f19c328e98a13066d0366935887fe589a9`。
- 新增 RunPod registry 凭据 `cmtgxws1c003d14njrtc07zd2`（GHCR，`read:packages`
  classic PAT，owner ericstory），已挂到 `km9g8f4guq` 与 `wjxhc0dtid` 两个模板。
  背景：RunPod 共享出口 IP 的 GHCR 匿名拉取配额经常耗尽（`toomanyrequests`），
  匿名拉取导致 LTX 5 次建 Pod 失败；认证拉取实测 2.5 分钟完成。PAT 过期后需轮换。

## 资源/费用

- 运行中 Pod：`0`；仅网络卷存储费（KS2/NE1/NC2 共 7 卷，约 $11.90/月 新增部分见旧记录）
- 账户 8 月 GPU 累计约 `$4.96`（本轮对照 + 缺陷修复重跑约 `$3.2`）

## 模板/镜像

- Wan 模板：`wjxhc0dtid` → 镜像 `c11714b5340edc4ed8a0f1be5791c983fd7a1bee`
  （含 SageAttention v2.2.0 SM12.0 源码编译；env `WAN_ATTENTION_BACKEND=sage_attn`）
- LTX 模板：`km9g8f4guq` → 镜像 `87901bc5d36164d54f211b94efdc2f3165b8a2b8`
- 两模板 registry 凭据：`cmtgxws1c003d14njrtc07zd2`

## 加速与监控（2026-08-31 追加）

- Wan 链路监控上线：worker 各阶段经 `POST /generate/api/internal/pod-progress/{task_id}`
  实时回报，`provider_metadata` 携带 model_load/video/audio/upload 拆分计时与后端元数据，
  UI 详情页显示实时阶段。Railway 部署 `b1921258`。
- sage_attn 实测（480p/4s）：纯视频 227.9s（基线 ~281s），端到端 7.1 分钟（基线 13.8），
  单次 ~$0.25。详见 `ab-wan-ltx-rtx-pro-6000.md` 追加章节，含 720p/12s 不可行结论与
  参数上限换算表。
- 实际事故记录：用户 720p/12s 任务必然触发 30 分钟上限，已按用户决定取消止损（~$0.56）。
  UI 暂无参数上限提示，建议后续在前端对超上限组合给出预估与警告。

## Lightning fast profile（2026-08-31 晚追加）

- Wan 现为 4 步蒸馏 fast profile：模板 adapter 指向 `fused-lora/{high,low}_adult_lightning_v1.safetensors`
  （成人+Lightning 离线融合单 adapter，dynamic 模式），steps=4、CFG 1.0/1.0、flow_shift=5.0、
  workflow `…lightning4fused…-v7`，镜像 `d7cd5ba1…`。
- 实测：480p/4s 端到端 4 分 44 秒；720p/10s 端到端 ~7 分钟。
- **⚠️ 720p/12s 输出为全黑**（sglang FFN int32 索引溢出，tokens>155k 触发；隔离矩阵见
  ab 文档）。已在 worker（token 预算校验）与 web（提交拦截）双层封锁：**Wan 720p
  上限 10 秒**；12–15 秒长片走 LTX。画质待用户盲评，回切 40 步质量档只需还原模板 env。
- sglang v0.5.16 两个已确认限制（勿踩）：dynamic 每 target 单 adapter；merge 模式对
  modelopt-FP8 有维度 bug。
- Railway Wan 通道当前主/兜底均为 **US-NC-2**（`nv7g5aobqn`），KS2 融合文件补齐后应恢复
  KS2 主（`RUNPOD_WAN_POD_DATA_CENTER_ID=US-KS-2`、`NETWORK_VOLUME_ID=3xl6dvrx0p`、
  fallback NE1 需先补 lightning+fused 文件或保持禁用）。
- 运维教训：一次性 Pod 的删除保底必须独立于本地会话进程（本轮 NC2 下载 Pod 因守护进程
  被清理多计费 ~70 分钟 ≈$2.7）。

## 下一步建议

1. 用户人工验收两个视频（`/generate` 任务列表即可播放/下载；LTX one-shot 的视频不在
   任务 DB，仅在卷上：`d5ce95fd-c283-41ee-a7eb-248ccdabeeef.mp4`）。
2. 启动 `video-pipeline-v2.md` 的固定 A/B 矩阵（6–10 提示词 × 3 seed，人工盲评）。
3. 可选改进：Wan handler 拆分模型加载与纯推理计时；LTX one-shot 增加回调以纳入任务 DB。

不要把 RunPod API key、GitHub PAT、Railway 管理员密码或上传 token 写入本文件或命令输出。
