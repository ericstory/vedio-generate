# RunPod 链路交接记录（2026-09-02 更新）

## 当前状态一句话

MiniMax H3 主线的代码、控制面、模型权重、Railway 部署全部就位，UI 里能选到 H3，
**但从未成功出过一次片**。

唯一的硬阻塞是 **H3 镜像的 CI 构建**：连续五次失败，根因未确诊，
所以没有一个包含 volume-free 能力的可用镜像。GPU 可用性问题已经设计并实现了解决方案
（volume-free，任意 DC × 任意合适卡），但因为没有镜像，**未经端到端验证**。

**下一个会话的第一件事：拿一个能读 GitHub Actions 日志的 token**（见第四节）。

---

## 一、决策与选型（已定）

主线从 Wan 2.2 换成 **MiniMax H3**，依据见
[`model-gpu-selection-2026-09.md`](model-gpu-selection-2026-09.md)：
H3 是 Artificial Analysis 盲评开源第一（T2V Elo 1301 无音频 / 1227 有音频，总榜第 4），
原生 32kHz 立体声一次出片，PinkCherry 作者已把整套 NSFW 微调迁过来。

链路角色：

| 代号 | 模型 | 状态 |
| --- | --- | --- |
| 主线 | MiniMax H3 + PinkCherry + lightx2v 8 步 turbo | 代码就位，**未跑通** |
| 兜底 | PinkCherry LTX 2.3 | 生产中（仍在 H100 serverless） |
| 对照 | Wan 2.2 A14B | 保留不再投入 |

用户已拍板的产品形态：UI 里给**两个 NSFW 能力**——
「H3 · 10Eros Max 快速档」和「H3 · PinkCherry 质量档」。保温方式选 **A：纯自动空闲超时**。

---

## 二、已完成并提交（main 分支）

| 提交 | 内容 |
| --- | --- |
| `96fba7e` | H3 worker（sglang `DiffGenerator` 原生，无 ComfyUI、无第二音频模型）+ 控制面 provider |
| `8d5e397` | H3 镜像 CI workflow |
| `a7f466c` | 下拉里 H3 排第一、LTX 标为兜底 |
| `9fff2c1` | 资源清单 + 存储成本口径更正 |
| `214c818` | 容量失败重试 3 轮 + 容量错误不再误报为"提示词违规" |
| `5c8e6f0` | 显存自适应常驻档位 + GPU 候选列表 |
| `9b75b4f` | **volume-free**：卷为空则不钉 DC，worker 开机自己下权重 |
| `e4522a1` / `8527785` / `13de95d` | 构建失败的三轮排查（SM 10.0 / 缓存 / 退回单架构，**全部未解决**）|
| `aa093a5` | 本交接文档 |

测试 82 个全过。

---

## 三、线上资源（实际存在的东西）

**Railway**（project `aigirl` / service `video-generator`）：已部署，`H3_ENABLED=1`，
UI 下拉里 H3 可选且排第一。相关变量已全部设置，含 `HF_TOKEN`（用户 `Andrew3453`
的 fine-grained token，对 `user/Andrew3453` 有 `repo.write`）。
`WAN_V2_ENABLED` 仍为 `1`——**故意的**，H3 验证通过前不撤掉可用的 Wan。

**RunPod**：

| 资源 | ID | 备注 |
| --- | --- | --- |
| H3 卷（主） | `n7meo4oft2` | US-NC-2 170GB，已灌满 145,443,261,098 字节 |
| H3 卷（备） | `qextiwmyla` | US-KS-2 170GB，已灌满 145,443,261,107 字节 |
| H3 模板（旧） | `z2jlkzb9bt` | 挂卷版，指向 `8d5e397` 镜像，**volume-free 后需重建** |
| LTX 模板 | `km9g8f4guq` | |
| Wan 模板 | `wjxhc0dtid` | |
| GHCR 凭据 | `cmtgxws1c003d14njrtc07zd2` | `read:packages` classic PAT |

**当前运行中 Pod：0。** 所有临时/探测 Pod 均已删除。

⚠️ **9 个卷 / 1000GB ≈ $70/月，7×24 计费。** volume-free 跑通后按下面"待清理"处理。

---

## 四、⛔ 未解决：H3 镜像 CI 构建失败

| 提交 | 架构列表 | 缓存 | 构建步骤耗时 | 结果 |
| --- | --- | --- | --- | --- |
| `8d5e397` | `12.0` | 有（冷） | **36.9 分钟** | ✅ 成功 |
| `5c8e6f0` | `8.9;9.0;10.0;12.0` | 有 | 5.9 分钟 | ❌ |
| `9b75b4f` | 同上 | 有 | 4.3 分钟 | ❌ |
| `e4522a1` | `8.9;9.0;12.0` | 有 | 8.6 分钟 | ❌ |
| `8527785` | `8.9;9.0;12.0` | **无** | 4.4 分钟 | ❌ |
| `13de95d` | `12.0`（退回到成功过的架构列表） | 无 | — | ❌ |

⚠️ **最后一行是最重要的信息**：退回到与 `8d5e397` 完全相同的架构列表**仍然失败**，
所以**架构列表不是原因**，"SM 12.0 是已知可构建配置"这个说法现在也不成立了。

`8d5e397`（唯一成功）与 `13de95d`（失败）之间剩下的差异只有三项，下一个会话应从这里查：

1. pip 层加了 `huggingface_hub[cli]>=0.35,<1`（volume-free 需要 `hf` 命令）
2. 移除了 `cache-from` / `cache-to`
3. `handler.py` / `download_models.sh` 的内容变化（只是 COPY，最后一层，理论上无风险）

也不能排除**环境变化**：GitHub 托管 runner 的可用磁盘、或上游某个依赖在两次构建之间变了。
基础镜像是按 digest 钉死的，可以排除。

**四个被实验推翻的假设，不要重复走**：

1. ~~CUDA 13 floor 缩小了宿主机池~~ —— 去掉 `minCudaVersion`、去掉卷、不限 DC、连
   community 都试，RTX PRO 6000 照样分不到。**是真缺货，不是过滤条件。**
2. ~~SM 10.0 不被支持导致失败~~ —— SageAttention v2.2.0 的 `setup.py` 里
   `SUPPORTED_ARCHS = {8.0, 8.6, 8.9, 9.0, 12.0}` 确实没有 10.0 且会 `raise`，
   **这是真 bug 必须避开**，但去掉它之后仍然失败。
3. ~~GHA 层缓存撑爆 runner 磁盘~~ —— 相关性很强（唯一成功的是唯一无缓存的），
   但移除 `cache-to` 后 4.4 分钟同样失败。**推翻。**
4. ~~多架构编译太重~~ —— 退回单架构 `12.0`（与成功那次完全一致）**仍然失败**。**推翻。**

复现 Pod（`lmsysorg/sglang:v0.5.18-cu130`，cpu3c×8，80GB 盘）排除了资源问题：
80GB 全空、754GB 内存、CUDA 13.0 正常。它自己死在无关的 `git clone` 认证失败
（`could not read Username for 'https://github.com'`）——**顺带暴露一个待修的脆弱点：
Dockerfile 用 `pip install git+https://...` 装 SageAttention，网络受限环境会挂。
改成装固定 commit 的 codeload tarball 可以把 git 从构建路径去掉。**

**下一步要做的第一件事**：拿一个有 `repo` 权限的 GitHub token 读 Actions 日志。
本机只有 SSH push 凭据（`~/.config/gh` 不存在，keychain 里没有 github.com 条目），
RunPod 那个 GHCR PAT 只有 `read:packages`，权限不够。没有日志就只能二分猜，
今天已经因此浪费了**五次**构建、推翻了四个假设。匿名调 GitHub API 还会撞限流
（本次会话末尾已撞上），有 token 也能顺带解决。

拿到日志前**不要再改 Dockerfile 试**——五次构建的经验是盲改只会消耗时间。

顺带一个已知待修的脆弱点（不一定是本次根因，但值得改）：Dockerfile 用
`pip install git+https://...` 装 SageAttention，`git clone` 在受限网络会尝试交互式认证
然后失败（复现 Pod 上实际发生过）。改成装固定 commit 的 codeload tarball
（`https://github.com/thu-ml/SageAttention/archive/<sha>.tar.gz`）可以把 git 从构建路径去掉。

---

## 五、架构决策：为什么放弃网络卷

**卷把 Pod 钉死在一个 DC，而那个 DC 没有我们能用的卡。** 实测：

```
7 张候选卡 × US-NC-2（挂卷）  = 14 次尝试全部 "no instances available"
7 张候选卡 × US-KS-2（挂卷）
同样这些卡不挂卷： RTX 5090 → EUR-IS-1 ✅  PRO 4500 → EU-RO-1 ✅  L40S → US-TX-4 ✅
200GB 容器盘 + 不挂卷：三张卡三个 DC 全部秒建成功 ✅
```

所以让**便宜的存储去约束昂贵稀缺的 GPU，方向是反的**。volume-free 后：

- 放置范围：任意 DC × 任意合适卡
- 代价：每次冷启动下 145GB，实测约 5 分钟
- 对比：Wan 的网络卷加载实测 90–805 秒抽奖，**卷并不更快**
- 顺带：存储从 $70/月 降到约 $0

`stockStatus` 不能用来判断能否建 Pod——RTX PRO 6000 全程显示 "Low"，14 次全失败。
**只有真的去建才算数。**

---

## 六、成本口径（更正过的）

2026-08-02 → 09-02 实际账单 `$44.11`：GPU 74%、serverless 附加费 15%、存储 10%、
容器盘 0.2%。

**但那个存储数字有误导性**——卷是月底才建的。按最后一个完整日 run rate：
`$1.42/天 ≈ $43/月`（当时 660GB），现在 1000GB ≈ **$70/月**。

`$70/月存储 = 33.5 小时 GPU = 每月 167 个视频（冷启动档）`。
**除非每月超过 167 个视频，否则存储比 GPU 贵**，而且存储 7×24 无条件计费。

另：**serverless 有约 49% 附加费**（GPU $13.62 + fee $6.67），所以一律走按需 Pod。

---

## 七、延迟问题与保温方案（已设计，未实现）

冷启动预算（基于实测）：建 Pod 0.5 min + 拉镜像 14.3GB 约 2.5 min + 取权重约 5 min
+ 装显存 2–3 min + 推理 0.5–2 min ≈ **每个任务 10 分钟固定开销**。

**核心矛盾不是 GPU 稀缺，是每个任务都开一次性 Pod。**

按 $2.09/h 算，冷启动每个视频白烧 $0.35；一小时做 6 个 = 浪费 $2.10，
而让 Pod 热着一小时 = $2.09。**超过 2–3 个视频/小时，保温就更划算。**

设计（用户选了 A：纯自动空闲超时）：worker 装完模型后向控制面**轮询**下一个任务
（复用现有 token 鉴权，不需要给 Pod 开入站端口），空闲 N 分钟后控制面删 Pod。
建议窗口 10–15 分钟。

---

## 八、10Eros Max（第二个 NSFW 能力，已调研未实现）

`TenStrip/10Eros-Max`（451 likes，比 PinkCherry H3 的 368 高）。
LTX 代的 `TenStrip/LTX2.3-10Eros` 有 562 likes / 7.9 万下载。

**纠正一个常见误解**：它快是因为 **TURBO 蒸馏被烤进权重**
（`10Eros_Max_h3_TURBO-hybrid_beta4.safetensors`，元数据 `H3 LoRA merge`），
**不是因为量化**。int8/nvfp4/GGUF 变体是给 ComfyUI 低显存用户的，我们的 sglang 栈
自己做在线 FP8，既不需要也读不了那些格式。

**阻塞**：所有 10Eros Max 变体（含 40.22GB 的 bf16）都用**裁剪过的 AdaLN**：

```
官方/PinkCherry : blocks.N.adaln_proj.linear.weight [96768, 2688]
10Eros Max      : blocks.N.adaln_proj.linear.weight [96768,    8]
                + adaln_basis[8,2688] + adaln_mean[2688] + adaln_t_table[1025,8]
```

sglang v0.5.18 的 H3 DiT 喂进 `adaln_proj` 的是完整 2688 维 `SiLU(t_emb)` 且有形状校验；
`minimax_h3_adaln_online` 是**按请求预算 AdaLN 的缓存策略**，不是 pruned 格式支持。
**sglang 装不下。**

**但可离线还原，数学已验证精确**（下载那几个小张量实测
`max |basis @ basis.T − I| = 0.003`，纯 bf16 舍入噪声，即正交）：

```
full_W    = pruned_W @ basis          [96768,8] @ [8,2688] → [96768,2688]
full_bias = pruned_bias − full_W @ mean
```

产物约 66GB（AdaLN 占 26GB，`66.28 − 40.22 = 26.06` 正好对上），
传到 `Andrew3453` 的 HF 私有仓库，volume-free worker 直接下载。
HF token 已在 Railway 变量里，权限够。

---

## 九、下一步顺序

1. **拿 GitHub token**（`repo` 权限）→ 读 Actions 日志 → 确诊构建失败根因
2. 构建修好后 → 用新 SHA 建 **volume-free 模板**
   （`ROOT=/models/MiniMax-H3-PinkCherry`、`disk=220`、带 `HF_TOKEN` 和
   `H3_DOWNLOAD_ON_START=1`，脚本见 scratchpad `mktemplate.py`）
3. Railway 设 `RUNPOD_H3_POD_NETWORK_VOLUME_ID=`（**留空**）+
   `RUNPOD_H3_POD_ADDITIONAL_GPU_IDS` + 新模板 ID → 部署
4. **跑第一次真实出片**（走生产 API，验证回调 / 进度 / 自动删 Pod 三件事）
5. 成功后：用实测填 `H3_SECONDS_PER_MPIXEL_STEP` 打开 30 分钟超时守卫
6. 保温（拉取式 worker + 自动空闲超时）
7. 10Eros Max AdaLN 离线还原 → 私有仓库 → UI 两个能力
8. 清理：删 9 个卷（省 $70/月）、8 个闲置模板、5 个残留 serverless endpoint。
   ⚠️ **LTX 的 NE-1 卷 `fn6at7unxa` 不能删**——生产 endpoint `aoma1602mogius`
   还挂着它，要等 V1 迁到 Pod 链路之后
9. V1 LTX 从 serverless 迁到按需 Pod（单价 $4.79/h → $2.09/h，且免掉 49% 附加费）

---

## 十、别再踩的坑

- 查 RunPod 库存**必须带 `secureCloud:true`**，否则看到的是 community 价和假缺货
- `stockStatus` 与"能否建 Pod"无关，只有真去建才算数
- 容量拒绝**零成本**（Pod 没建出来），可以放心重试
- 下载权重用 **CPU pod**（$0.03/vCPU/h），别用 GPU pod：145GB 约 5 分钟，
  守护失效跑一天也就几美元，GPU pod 同样疏忽是 $50/天
- 判断第三方权重能否被 sglang 加载：**HTTP Range 读 safetensors 头部**比对键名和形状，
  零成本且确定。带 `comfy_quant` 张量的是 ComfyUI 量化，sglang 加载不了
- turbo LoRA 的步数是**名字 +1**（4 步档用 5、8 步档用 9），flow schedule 需要 N+1 个 sigma
- 不要把 API key、PAT、上传 token 写进文件或命令输出
