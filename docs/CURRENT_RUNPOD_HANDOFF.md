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

## 画质反馈与当前档位（2026-09-01）

- 用户实测 4 步蒸馏画质不达标。已把 fast profile 升到 **8 步**
  （workflow `…lightning8fused…-v8`，仅模板 env 变更），画质/速度中点，待用户复测。
- 若 8 步仍不达标：模板还原三项即可回 40 步质量档（adapter 路径换回
  `adult-lora/NSFW-22-*.safetensors`、steps=40、CFG 4.0/3.0、去掉 WAN_FLOW_SHIFT）。
- 正解（下个会话）：**按请求选质量档**——UI 加"快速/质量"开关，payload 带 steps/profile
  透传到 worker，两档共存不再全局切换。

## 硬件统一决策（用户 2026-09-01 拍板）

V1（LTX）、V2（Wan）、未来新链路统一使用 `NVIDIA RTX PRO 6000 Blackwell Server Edition`。
现状：V2 已是；**V1 生产链路仍在 H100 serverless（NE1）**，需迁移为 RTX PRO 6000
一次性 Pod 模式（模板 `km9g8f4guq` 与 KS2/NC2 卷已就绪，缺的是把 web 的 LTX provider
从 serverless 改为 pod 链路 + 回调，参照 Wan pod provider 实现）。

选型复核见 [`model-gpu-selection-2026-09.md`](model-gpu-selection-2026-09.md)：
96GB 不只是够用，而是 RunPod secure 上唯一有真实供给的档位（48GB 及以下的卡在 21 个
支持网络卷的 DC 里几乎零库存）。另外查实 **serverless 有约 49% 附加费**，V1 迁到
按需 Pod 后单价从 `$4.79/h` 降到 `$2.09/h`。

## 主线切换：MiniMax H3 + PinkCherry（2026-09-01 代码已就位，未上机）

用户拍板把主线从 Wan 2.2 换成 MiniMax H3。理由：H3 是 Artificial Analysis 盲评的
**开源第一**（T2V Elo 1301 无音频 / 1227 有音频，总榜第 4，只输给闭源的 Wan 3.0 和
Seedance 2.0），原生 32kHz 立体声一次出片，而且 V1 用的 PinkCherry 的作者
（`SexGod1979`）已经把整套 NSFW 微调迁到了 H3。

- 新 Worker：`workers/minimax-h3/`，SGLang Diffusion 原生 `DiffGenerator`，
  **不依赖 ComfyUI**、不起 server、**没有第二个音频模型**（AudioLDM2 那一段整段删掉）。
- 基座 `MiniMaxAI/MiniMax-H3` FL2VA 分区 + PinkCherry beta-0.6 完整微调 DiT
  （经单文件 `transformer_weights_path` 覆盖）+ lightx2v 8 步蒸馏 LoRA。
- PinkCherry 的可加载性是**读 safetensors 头部验证过的**：535 个张量，名称/形状与官方
  FL2VA transformer 逐项一致。仓库里的 `int8_convrot` 变体是 ComfyUI 量化，不可用。
- 控制面：新 provider `runpod_h3_pod`，与 Wan 共用一次性 Pod 契约（价格上限、
  30 分钟超时、终态回调删 Pod、进度回调）。UI 选项由 `H3_ENABLED` 开关控制，默认关。
- 卷：默认 145.4GB（跳过被 PinkCherry 替换掉的 66.28GB 官方分片），建议 170GB 单卷。

**还没上过机**。首轮要花钱确认的 5 件事写在 `workers/minimax-h3/README.md` 末尾，
最关键的是峰值显存、单次耗时、在线 FP8 之后 merge LoRA 是否正确。

### H3 资源清单（2026-09-01 建，方案 B）

| 资源 | ID | 说明 |
| --- | --- | --- |
| 卷（主） | `n7meo4oft2` | US-NC-2，170GB，已灌满 |
| 卷（备） | `qextiwmyla` | US-KS-2，170GB，已灌满 |
| 镜像 | `ghcr.io/ericstory/papa-minimax-h3:<sha>` | GHCR，registry 凭据 `cmtgxws1c003d14njrtc07zd2` |
| 模板 | 见 Railway `RUNPOD_H3_POD_TEMPLATE_ID` | `args={"cmd":["python","/app/smoke.py"]}` |

两卷内容实测一致：`145,443,261,098` / `145,443,261,107` 字节，与 `models.lock.json` 的
`expected_download_bytes`（145,443,193,257）差约 68KB，即 HF 的 `.cache` 元数据。

下载用 **CPU pod**（`python:3.12-slim` + 公开仓库里的 `download_models.sh`），不是 GPU pod：
145GB 约 5 分钟下完，单价 `$0.03/vCPU/h`，就算守护失效跑一整天也只有几美元，而 GPU pod
同样的疏忽是 `$50/天`。KS-2 首次申请 8 vCPU 直接返回"无实例可用"，降到 4 vCPU 才建上——
下载这种可重试的活儿不该占 GPU 配额。

### 存储成本的实际口径（更正）

`/v2/billing` 窗口总额会低估：卷是月底才建的，只计了几天。按最后一个完整日算 run rate：
2026-08-31 是 `$1.42/天 ≈ $43/月`（660GB）。加上 H3 两个 170GB 卷后约 **$70/月**。
H3 冒烟通过后按方案 B 清理：删 Wan BF16 遗留卷 `p7dkzdmomf`（NE-1 150GB）、3 个 Wan FP8
卷（210GB）、LTX 冗余卷，目标回到约 `$28/月`。**LTX 的 NE-1 卷 `fn6at7unxa` 不能删**——
V1 生产 serverless endpoint `aoma1602mogius` 还挂着它，要等 V1 迁到 Pod 链路之后。

## 下一步建议

1. 建 H3 的 170GB 网络卷（建议 US-NC-2，当前 RTX PRO 6000 secure 库存最好）+ 一次性
   模板，跑 `download_models.sh`，然后单 Pod 冒烟 1 次确认能出片。
2. 用实测填 `H3_SECONDS_PER_MPIXEL_STEP`，把 30 分钟超时守卫打开——Wan 就是栽在
   没有这个守卫上（720p/12s 必超时，计费后才失败）。
3. H3 vs LTX 2.3 的人工盲评（H3 通过后再谈把 Wan 下线）。
4. V1 LTX 从 serverless 迁到按需 Pod（省 56% 单价，且不再交 serverless 附加费）。

不要把 RunPod API key、GitHub PAT、Railway 管理员密码或上传 token 写入本文件或命令输出。
