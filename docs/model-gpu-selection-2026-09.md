# NSFW 视频链路：模型 + 硬件选型定稿（2026-09-01）

目标：找到**质量最高、出片最快**的 NSFW 视频生成组合，然后固定下来不再折腾。

两条硬判据（用户定义）：

1. 模型必须有 NSFW LoRA / 微调补丁生态 —— 闭源 API 一律出局；
2. GPU 要性价比最优，同时满足质量和速度。

本文所有价格、库存、下载量、Elo 均为 2026-08-31/09-01 实时拉取，来源见文末。

---

## 一、硬件结论：RTX PRO 6000 Blackwell Server Edition 96GB，$2.09/h secure

**这不是"想要大显存"的偏好，是供给数据逼出来的唯一解。**

RunPod secure cloud 在 21 个支持网络卷的数据中心里的实时库存（多次采样）：

| GPU | VRAM | secure $/h | 有货 DC 数 | 备注 |
| --- | --- | --- | --- | --- |
| **RTX PRO 6000 Blackwell Server Ed** | **96GB** | **2.09** | **4–8** | SM120，供给最好 |
| RTX PRO 6000 Workstation Ed | 96GB | 1.89 | 0–2 | 同 SM120，可当兄弟卡兜底 |
| RTX PRO 5000 Blackwell | 48GB | 0.96 | **0** | 全网 secure 无货 |
| L40S | 48GB | 0.99 | 1 | 仅 EU-NL-1 |
| RTX 5090 | 32GB | 0.99 | 1 | 仅 EUR-IS-1 |
| RTX 4090 | 24GB | 0.74 | 5 | 太小，只能重度 offload |
| A100 80GB PCIe | 80GB | 1.39 | 1 | SM80，**不支持 FP8/NVFP4**，出局 |
| H100 SXM / NVL | 80/94GB | 3.29/3.19 | 1–4 | 更贵、更少、显存更小 |
| H200 | 141GB | 4.59 | 3–8 | 供给好但 2.2× 价格 |
| B200 / H200 NVL | 180/143GB | 6.79/3.79 | 0 | secure 全网无货 |

关键结论：

- **"用便宜小卡省钱"在 RunPod 上不成立。** 48GB 及以下的卡在北美 secure 基本零库存，
  真要用只能去欧洲单点，反而更不稳定。96GB 这张恰恰是供给最好的。
- **SM120（Blackwell）是硬需求**：NVFP4 权重、FP8 tensor core、SageAttention 2.2 都要它。
  A100（SM80）直接出局；H100/H200（SM90）需要重编 SageAttention 才能用。
- 96GB 给足余量，不用再为峰值显存做参数封锁（对比 V2 Wan 曾实测 91.3GB 峰值贴着上限跑）。

### 顺带查出来的两个真实成本问题

1. **Serverless 有约 49% 的附加费。** 2026-08 账单：serverless GPU `$13.62` + serverless fee
   `$6.67` = `$20.29`，而按需 Pod GPU 只有 `$19.19`。V1 现在跑的 serverless `ADA_80_PRO`
   池是 `$4.79/h`，同期 RTX PRO 6000 按需 Pod 只要 `$2.09/h`。**全部走 Pod，不用 Serverless。**
2. **网络卷不是成本问题**：8 个卷整月只有 `$4.34`。但 7 个卷（KS2/NE1/NC2 × LTX/Wan 混布）
   是运维复杂度问题，该砍。目标砍到 **2 个卷**，只留库存最好的两个 DC。

### 定价口径纠错

之前文档里记的 `$1.69/h` 是 **community 价**。我们建 Pod 用的是 `cloud: SECURE`，
真实单价一直是 `$2.09/h`。查库存时不带 `secureCloud:true` 会看到假的"无货"——
US-KS-2 / US-NE-1 就是这么被误判的。

---

## 二、模型结论：主攻 MiniMax H3

Artificial Analysis 视频竞技场（盲评 Elo）+ NSFW 生态实测数据：

| 模型 | 开源 | T2V Elo（无音频/有音频） | NSFW 生态 | 原生音频 | 权重体积 |
| --- | --- | --- | --- | --- | --- |
| **MiniMax H3** | ✅ 2026-07-28 | **1301 / 1227**，开源第一、总榜第 4 | 19 个专用 LoRA、80 万下载，1 个月内起量 | ✅ 32kHz **立体声** | nvfp4 ≈34GB / fp8 ≈54GB / bf16 ≈124GB |
| LTX-2.5 | ✅ 2026-07-23 | 1214 / 1060 | 仅 4 个 NSFW LoRA，很新 | ✅ | nvfp4 ≈37GB / bf16 ≈79GB |
| Wan 2.2 A14B（现 V2） | ✅ | 未上榜 | **最深**（单个 LoRA 达 49 万下载） | ❌ 需 AudioLDM2 后处理 | FP8 45GB，实测峰值 91.3GB |
| LTX-2.3（现 V1） | ✅ | 未上榜 | 成熟（PinkCherry 28 万下载） | ✅ | — |
| HunyuanVideo | ✅ | 未上榜 | **已死**，HF 上最后活跃 2024-12 | ❌ | — |
| Wan 2.6 / 2.7 / 3.0 | ❌ 闭源 | 3.0 = 1242（总榜第 1） | 不可能加 LoRA | — | — |
| Seedance 2.0 / Hailuo API | ❌ 闭源 | 1221 | 不可能加 LoRA | — | — |

> ⚠️ 辟谣：多篇 SEO 文章声称"Wan 2.7 于 2026-03 以 Apache 2.0 开源"。**不实。**
> Wan-AI 官方 HF 组织最新只到 `Wan2.2-Animate-2-14B`（2026-08-06），
> Wan 2.6/2.7/3.0 在 Artificial Analysis 上均标注为 Proprietary。**Wan 2.2 仍是最新的开源 Wan。**

### 为什么是 MiniMax H3

1. **开源质量第一，且差距明显。** 无音频 T2V Elo 1301 vs LTX-2.5 Fast 1214；
   有音频 1227 vs LTX-2.5 1060。总榜只输给闭源的 Wan 3.0（1242）和 Seedance 2.0（1221）。
2. **NSFW 生态正在迁移过来，而且是同一批人。** `SexGod1979`——我们 V1 用的
   PinkCherry LTX 2.3（28 万下载 / 252 likes）的作者——已经发布：
   - `PinkCherry_MiniMax-H3`（2026-08-05，**368 likes**，比 LTX23 版还高，只用了 3 周）
   - `AfterMidnight-MiniMax-H3-NSFW`（2026-08-18，102 likes）
   - `PinkFluffyBunny-MiniMax-H3`（99 likes）

   Civitai 上 H3 专用 NSFW LoRA 已有 19 个 / 80 万下载（Mystic XXX 5.6 万、
   HMNSFW AIO Sex 4.9 万、HMPussy 3.0 万、SexGod NaughtyTimes 2.3 万、H3 Motion Booster 2.1 万…）。
3. **原生同步音视频，一次出片。** 32kHz 立体声。直接删掉 V2 现在的 AudioLDM2 后处理段
   （那段实测占 44.2s 加载 + 4.8s 推理，还踩过 accelerate 缺失和 transformers 5 兼容两个坑）。
4. **加速生态已经现成**，且是我们熟悉的供应商：
   - `lightx2v/Minimax-h3-Turbo`（Apache-2.0，4 步/8 步蒸馏）—— 就是我们 Wan Lightning 的同一家
   - `alibaba-pai/MiniMax-H3-Acc-LoRAs`、`fal/MiniMax-H3-Realism-People-LoRA`（4.4 万下载）
5. **官方推荐配置正好是我们这张卡。** LMSYS 原话："run it locally on **2x 5090 or 1 RTX 6000**"。
   SGLang Diffusion **day-0 原生支持** H3——和我们 Wan worker 现在跑的是同一套 sglang 栈，
   可复用镜像、LoRA 转换、进度回调、成本熔断这些已经调通的工程件。
6. **显存压力远小于 Wan 2.2 FP8。** pruned nvfp4 权重 ≈34GB、pruned fp8 ≈54GB
   （Wan 是 45GB 权重 + 峰值 91.3GB）。96GB 上可常驻不 offload，
   而且不会再撞 sglang int32 索引溢出那个 720p/12s 全黑的坑。
7. **许可可用。** MiniMax H3 Community License：年营收 < 2000 万美元可商用，
   **无色情内容禁止条款**（只禁未成年人相关 + 一条宽泛的伦理条款），允许做模型衍生（LoRA）。
   限制：不得用输出去训练其它 AI 模型。

### 被否决的方案

- **LTX-2.5**（观察位）：质量第二、原生音频、nvfp4 能塞进 48GB，但 **NSFW 适配只有 4 个**、
  全是 8 月新发。生态没起来之前不做主力。它是 H3 之后的第一顺位备选。
- **Wan 2.2**（降级为对照组）：NSFW 生态最深是它唯一的优势，但没有原生音频、
  质量未上榜、峰值显存贴着 96GB 上限、且有已确认的 sglang int32 溢出导致 720p/12s 全黑。
  不再投入新工作量，保留现有链路做盲评对照。
- **LTX-2.3**（保留兜底）：12–15 秒长片目前只有它能稳定出，H3 验证通过前不下线。
- **HunyuanVideo 1.5**：NSFW 生态已死（HF 上相关 LoRA 最后活跃 2024-12，下载量归零），
  判据 1 直接不满足。
- **闭源 API**（Wan 2.6/2.7/3.0、Seedance 2.0、Hailuo API）：无法加 NSFW LoRA，
  判据 1 不满足，且内容策略必然拦截。

---

## 三、落地路线

链路重新定义，从"V1/V2/V3 并列试"改成"一主一备一退役"：

| 代号 | 模型 | 角色 | 硬件 |
| --- | --- | --- | --- |
| **主线** | MiniMax H3 + PinkCherry/AfterMidnight NSFW LoRA + Turbo 4/8 步 | 新主力 | RTX PRO 6000 96GB 按需 Pod |
| 兜底 | PinkCherry LTX 2.3（现 V1） | 12–15 秒长片，H3 验证通过前不动 | 同上（从 serverless 迁到 Pod） |
| 对照 | Wan 2.2 A14B（现 V2） | 仅供盲评对照，不再投入 | 同上 |

存储：**7 个卷砍到 2 个**——一个 H3 卷（fp8 档约需 60GB，nvfp4 档约需 40GB），
一个 LTX 卷。放在库存最好的两个 DC（当前是 US-NC-2 和 EUR-IS-1，或 NC-2 + KS-2）。

同时要做的三件事（都不改模型）：

1. **停用 Serverless，全部走按需 Pod** —— 省掉 49% 附加费，V1 单价从 `$4.79/h` 降到 `$2.09/h`。
2. **选卡白名单从 1 张扩到 2 张** —— 加 `RTX PRO 6000 Blackwell Workstation Edition`
   （同 SM120 / 96GB，$1.89 更便宜），零改动扩供给。
3. **DC 通道按实时库存排序** —— 别再把 US-KS-2 写死成主通道。

---

## 四、还没验证的（第一轮要花钱确认的事）

以下都是**估算或第三方数据**，没有我们自己的实测：

1. H3 在 RTX PRO 6000 96GB 上的真实峰值显存与每步耗时（第三方数据：RTX 5090 4 步约 19s；
   RTX 4090 24GB 带 offload 约 8.5s/步；sglang 双 5090 在 1344×768/124 帧/50 步下 26.3 GiB/卡）。
2. H3 + PinkCherry NSFW LoRA 的实际画质（LoRA 只有 3 周历史，likes 高但下载量还小）。
3. sglang Diffusion 的 H3 LoRA 加载路径 —— 官方 cookbook 未提 LoRA，
   可能要走 ComfyUI 或自己转格式（我们在 Wan 上已有 comfy→peft 离线融合的成功先例）。
4. sglang 的 H3 serving **尚未进稳定发布契约**，版本要锁死并做 fail-fast 检查。
5. 4/8 步 Turbo 对 NSFW 场景的画质损失（Wan 上 4 步被用户判定不达标，升到 8 步）。

建议第一轮预算：单 Pod、$3/h 上限、30 分钟硬超时，跑 3–5 次共约 $2–4，
先确认「能跑通 + 峰值显存 + 单次耗时」，再谈画质盲评。

---

## 数据来源与采集时间

- RunPod GPU 目录与价格：`GET https://api.runpod.io/v2/catalog/gpus`（2026-08-31）
- RunPod 分 DC 实时库存：GraphQL `gpuTypes.lowestPrice(input:{secureCloud:true, dataCenterId})`，
  21 个支持网络卷的 DC，多次采样（2026-08-31 21:14–21:26 PT）
- 本账户账单：`GET https://api.runpod.io/v2/billing`（2026-08-02 → 09-02 窗口）
- 模型体积/发布时间/下载量：Hugging Face API（`MiniMaxAI/MiniMax-H3`、`Comfy-Org/MiniMax-H3`、
  `Lightricks/LTX-2.5`、`Wan-AI/*`、`SexGod1979/*`）
- NSFW 生态规模：Civitai API `models?types=LORA&nsfw=true`，按 baseModel 聚合
- 质量 Elo：[Artificial Analysis Text-to-Video Leaderboard](https://artificialanalysis.ai/video/leaderboard/text-to-video)
- 单卡可行性：[LMSYS 公告](https://x.com/lmsysorg/status/2084110114022396018)、
  [SGLang MiniMax-H3 cookbook](https://docs.sglang.io/cookbook/diffusion/MiniMax/MiniMax-H3)
- 许可：`MiniMaxAI/MiniMax-H3` LICENSE 原文

内容边界沿用既有规定：仅限合规的虚构成年人内容，拒绝未成年人、非自愿、
兽交/乱伦和真人无授权色情深伪。
