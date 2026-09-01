# Wan/LTX 同 GPU 对照实验记录（2026-08-30 PT / 08-31 UTC）

## 实验条件

- GPU：`NVIDIA RTX PRO 6000 Blackwell Server Edition`（96GB），Secure Pod `$2.09/h`
- 数据中心：两条链路均为 `US-KS-2`（Wan 主通道命中；LTX 用 KS2 卷 `i7seye8y8v`）
- 统一参数：4 秒、480p、16:9、`generate_audio=true`
- 统一提示词：`A calm sunrise over the ocean, gentle waves rolling toward the shore, warm golden light, cinematic wide shot`
- 统一 seed：`2288424372`（Wan 成功运行回报，LTX 复用）
- 控制面：Railway 部署 `f134cc56`（main `4374cdd`；后续 `d341704`/`ed4470f` 仅改 worker 镜像与测试，src 无变化）

## 对照结果

| 指标 | V2 Wan 2.2 A14B FP8 + 成人 LoRA | V1 PinkCherry LTX 2.3 v1.8 |
| --- | --- | --- |
| 任务/Pod | `6ec4dd3d` / `9tb7lfqpx1omfn` | one-shot / `s97os47os8slx5` |
| 提交链路 | 生产 API `runpod_wan_pod` | 模板 `km9g8f4guq` 手动 one-shot |
| 视频推理 | 去噪 40 步 273.4s（6.84s/步）+ 解码 7.7s；handler 视频段 778.5s（含加载/双 LoRA 合并，该宿主机卷吞吐偏慢） | 两阶段：20 步 384×224 + 3 步 768×448；handler 推理 370.0s（含模型加载与 Gemma 编码） |
| 音频 | AudioLDM2 19.1s，16kHz 环境声 AAC | 原生同步音视频（无独立音频段） |
| 峰值显存 | 65794 MB（64.3GB） | 未上报（BF16 + fp8-cast，96GB 内无压力） |
| 产物 | 65 帧 832×480 @16fps | 97 帧 768×448 @24fps |
| 端到端（建 Pod→终态） | ~13.8 分钟 | ~9.4 分钟（含 2.5 分钟认证镜像拉取） |
| 单次成本 | ~$0.48 | ~$0.33 |
| 视频 | `/generate/media/71afcf73-d18f-4de9-8891-39d3ef2cdbf8.mp4` | `/generate/media/d5ce95fd-c283-41ee-a7eb-248ccdabeeef.mp4` |

两个 MP4 均已上传 Railway 持久卷并验证可经管理员会话下载（HTTP 206）。质量盲评（提示词
遵循、结构、时间一致性等）留给人工验收；本记录只固定可比的客观指标。

注意分辨率/帧率语义差异：同为 "480p/4s"，Wan 输出 832×480@16fps/65 帧，LTX 输出
768×448@24fps/97 帧。跨模型 seed 只保证各自可复现，不构成逐帧可比。

## 过程中发现并修复的缺陷（自我验收产出）

1. **Wan 镜像缺 `accelerate`**（运行 1 `33o4viq2frjflf`）：AudioLDM2
   `enable_model_cpu_offload()` ImportError，发生在视频已完整生成之后。修复 `d341704`
   （Dockerfile 增加 accelerate 层 + 测试断言）。
2. **transformers 5 移除裸 GPT2Model 的 GenerationMixin 私有方法**（运行 2
   `ij9ke14yr33mx0`）：diffusers 0.40 AudioLDM2 rollout 调用
   `_get_initial_cache_position` 崩溃。修复 `ed4470f`：整体重绑
   `generate_language_model` 为 8 步无缓存公开 API 前向，不再依赖任何私有生成 API。
3. **GHCR 匿名拉取限流**（LTX 尝试 1–5，KS2/NC2 多宿主机均命中 `toomanyrequests`，
   45 分钟冷却无效——配额消耗方是共享出口 IP 上的全体用户）。根治：用户提供
   `read:packages` classic PAT，创建 RunPod registry 凭据 `cmtgxws1c003d14njrtc07zd2`
   并挂到 `km9g8f4guq`/`wjxhc0dtid` 两个模板；认证拉取实测 2.5 分钟完成。
4. 附带验证：REST v2 + `minCudaVersion=13.0` 在 NC2（CUDA 13.2）真实建 Pod 通过；
   Wan 四层成本保护（价格上限/精确 GPU/超时/回调删 Pod）三次运行全部生效。

## 成本

- Wan 三次运行：$0.38 + $0.56 + $0.48 ≈ $1.42
- LTX 六次尝试（5 次限流止损 + 1 次成功）：≈ $1.8
- 本会话合计 ≈ $3.2；账户 8 月累计 $4.96。终态 RunPod Pod 数为 0。

## 追加：SageAttention 加速与链路监控验证（2026-08-31）

镜像 `c11714b`（SageAttention v2.2.0 源码编译 SM12.0；PyPI 只有 1.x，必须 git 安装），
模板 env `WAN_ATTENTION_BACKEND=sage_attn`，handler 对 sage 后端做 fail-fast 导入检查
（sglang 缺包时会静默回退 FA，SM12.x 上不可靠）。

验证任务 `f291a89e`（480p/4s/40 步，seed 829404139，US-KS-2 同款 GPU）：

| 指标 | torch_sdpa 基线 | sage_attn | 变化 |
| --- | --- | --- | --- |
| 纯视频生成（不含加载） | ~281s（273.4 去噪 + 7.7 解码） | 227.9s | **≈1.23×** |
| 每步去噪（估算） | 6.84s | ~5.4s | ≈1.26× |
| 模型加载（首次单独计量） | 混在视频计时内（113–497s 波动） | 124.9s | 已拆分 |
| 音频（AudioLDM2 加载/推理拆分） | 19.1s（混计） | 44.2s 加载 + 4.8s 推理 | 已拆分 |
| 端到端（提交→完成） | ~13.8 分钟 | **7.1 分钟** | ≈1.9× |
| 单次成本 | ~$0.48 | **~$0.25** | ≈一半 |

结论：480p 短片下 sage 的每步收益约 1.25×（此分辨率序列短，注意力占比低）；端到端
收益主要来自认证镜像拉取与加载拆分后的可观测。注意力成本随分辨率/时长呈平方级增长，
**720p/长时长下 sage 的相对收益会显著放大**。

链路监控实测：`model_load_start → video_start → audio_model_load_start → complete`
阶段全部经 `pod-progress` 回调实时落入 `provider_metadata.progress`，UI 详情页显示
中文阶段文案；终态回调带全部拆分计时与后端/步数/CFG 元数据。

### 生产参数上限换算（30 分钟 Pod 上限）

实测教训：用户 720p/12s 任务在 torch_sdpa 下 40 步去噪估算 ≥60 分钟，触发 30 分钟
上限强制失败（已按用户决定手动取消止损）。基于 480p/4s 实测的粗略外推（误差可能大）：

- **480p/12s**：sage 下约 20–24 分钟，勉强可行（建议同时把步数降到 24–30）
- **720p/5s**：与上类似，边缘可行
- **720p/12s**：sage 下仍估算 ≥45 分钟，**不可行**；需要降步数 + 提高上限，或等
  sage_attn3（Blackwell FP4，需源码构建）/ distill LoRA 方案

## 追加：Lightning 4 步蒸馏 fast profile（2026-08-31 晚）

需求：Wan 链路 12 秒片 ~5 分钟出，保持强制成人 LoRA。方案：lightx2v
Wan2.2-Lightning Seko V2.0 4 步蒸馏 LoRA（revision `18bccf88`，SHA256 已入
models.lock）+ CFG 1.0/1.0 + flow_shift 5.0 + sage_attn。

实施途中命中 sglang v0.5.16 两个真实限制（各花一次付费运行确认）：

1. dynamic LoRA 每个 target 仅支持一个 adapter（四 LoRA 叠加被拒）；
2. merge 模式对 modelopt-FP8 底座存在维度 bug（`13824 vs 5120` FFN 张量不匹配）。

最终方案：**离线把成人 LoRA（peft 格式）与 Lightning LoRA（comfy 格式）低秩拼接为
单 adapter**（数学恒等；Lightning 块预除 0.9 抵消运行时全局强度，成人保持 0.9 等效、
Lightning 1.0 等效），继续走已验证的 dynamic 单 adapter 路径。融合在 Pod 上执行，
400/400 模块全对齐，输出确定性（多次重跑 SHA256 一致）：

- `fused-lora/high_adult_lightning_v1.safetensors` sha256 `d8828255c7ef…`
- `fused-lora/low_adult_lightning_v1.safetensors` sha256 `af75e5210cb0…`
- 已写入 KS2/NC2 两卷；workflow `wan22-t2v-fp8-lightning4fused-adult-lora-audio-v7`

验证结果（US-NC-2 同款 RTX PRO 6000）：

| 参数 | 端到端（提交→出片） | 纯视频推理 | 模型加载 | 峰值显存 | 成本 |
| --- | --- | --- | --- | --- | --- |
| 480p/4s | **4 分 44 秒** | 22.6s（基线 281s，≈12×） | 89.9s | 64.8GB | ~$0.17 |
| 720p/12s | **6 分 52 秒** | 230.3s（57.6s/步 ×4） | 134.2s | **91.3GB** | ~$0.24 |

此前 720p/12s 在 40 步档三次撞 30 分钟上限失败；fast profile 下首次通过。
91.3GB 峰值说明 **720p/12s 已接近 96GB 单卡上限**，720p/15s 大概率 OOM，先不放开。

注意：4 步蒸馏 + LoRA 融合是画质敏感变更，两个验证视频
（`41b5f652…mp4`、`7f2cbc52…mp4`）与真实成人提示词的效果需用户盲评验收；
40 步质量档可随时通过模板 env 回切（adapter 路径换回原文件 + 步数/CFG 还原）。

## 追加：720p/12s 黑屏根因（2026-08-31 深夜）

用户发现 720p/12s "成功"任务实为 193 帧纯黑（亮度恒 16，NaN 解码特征）。隔离矩阵
（每格一次付费运行 + ffmpeg 帧亮度分析）：

| 组合 | latent tokens | FFN 中间元素（×13824） | 结果 |
| --- | --- | --- | --- |
| 480p/4s | 25.9k | 3.6 亿 | 正常 |
| 720p/4s | 59.0k | 8.2 亿 | 正常 |
| 480p/12s | 76.4k | 10.6 亿 | 正常 |
| 720p/10s | 147.6k | 20.4 亿 | 正常 |
| 720p/12s | 176.4k | **24.4 亿 > 2³¹** | 全黑（sage 与 sdpa 均复现） |

结论：sglang 某内核对 FFN 中间张量使用 int32 索引，latent tokens 超过
~155k（2³¹/13824）即溢出产生垃圾 → NaN → 黑帧；与注意力后端、时长本身、FP8
校准均无关（各假设逐一被隔离实验排除）。防护已上线：

- worker `validate_token_budget`（上限 150k，留安全余量，未开始计费即拒绝）
- web 提交层直接拦截 Wan 720p >10 秒，提示改 480p 或 LTX
- 结论：**Wan 720p 上限 10 秒（6–7 分钟出片）；480p 全时长安全；12–15 秒长片走
  LTX（H100 serverless 实测 10.4 分钟）或未来分段续写**
- 应向 sglang 上游报告该 int32 溢出

## 后续

- 质量 A/B 正式矩阵（`video-pipeline-v2.md` 的 6–10 提示词 × 3 seed 盲评）在此基础上执行。
- 端到端还剩 ~3.5 分钟固定开销（建 Pod + 拉镜像 + 模型加载 134s）；要进一步压缩需
  warm pool / 常驻方案，或 [[本地 4090 V3 渠道]]（用户已提出，等机器信息）。
- 可选二期：SageAttention3（Blackwell FP4）、AudioLDM2 步数下调、sglang int32 上游修复后解锁 720p/12s。
