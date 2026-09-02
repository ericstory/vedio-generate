# RunPod 链路交接记录（2026-09-02 更新）

## 当前状态一句话

MiniMax H3 主线的代码、控制面、模型权重、Railway 部署全部就位，UI 里能选到 H3，
**但从未成功出过一次片**。

曾经卡住一整天的 CI 构建**根因已确诊并修复**（huggingface_hub 升级破坏了
SageAttention 的元数据生成，详见第四节）。修复提交 `d5dd9d5` 的构建在写下这段时
已跑过 5.4 分钟——远超之前 8 秒就挂的失败点——但**尚未确认成功**。

GPU 可用性问题已经设计并实现了解决方案（volume-free，任意 DC × 任意合适卡），
但因为一直没有可用镜像，**整条链路未经端到端验证**。

**下一个会话的第一件事：确认 `d5dd9d5` 的镜像构建结果，然后按第九节往下走。**

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
| `e4522a1` / `8527785` / `13de95d` | 构建失败的三轮排查（三个假设，**全部错了**）|
| `aa093a5` / `cb8725f` | 本交接文档 |
| `d5dd9d5` | **真正的修复**：不再升级 huggingface_hub，改用 Python API 下载 |

测试 83 个全过。

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

## 四、✅ 已确诊并修复：H3 镜像 CI 构建失败

**根因**（2026-09-02，拿到能读 Actions 日志的 GitHub token 后一次定位）：

```
File ".../huggingface_hub/dataclasses.py", line 332, in type_validator
    raise TypeError(f"Unsupported type for field '{name}': {expected_type}")
TypeError: Unsupported type for field 'import_name': str | None
error: metadata-generation-failed  ->  sageattention
```

`9b75b4f` 为了 volume-free 需要 `hf` 命令而装了 `huggingface_hub[cli]>=0.35,<1`，
**升级了 sglang 基础镜像自带的那份**；新版严格 dataclass 校验器在 SageAttention
生成包元数据时抛异常。构建死在那一层的**第 8 秒**，连一个 kernel 都没编——
所以改架构列表怎么都没用。

**修复**（`d5dd9d5`）：不装也不升级 huggingface_hub。sglang 本来就依赖它，
`download_models.py` 改用 **Python API**（`snapshot_download`）而非 `hf` 命令。
`download_models.sh` 退化为薄包装，仍可在独立 CPU Pod 上灌卷。
测试 `test_h3_image_does_not_disturb_the_base_huggingface_hub` 断言
Dockerfile 里永远不再出现 huggingface_hub 的安装行。

### 五次失败其实是三个不同原因

| 提交 | 真实原因 |
| --- | --- |
| `5c8e6f0` | **GitHub runner 被强制关机**（`The runner has received a shutdown signal`），wheel 编译跑到 77 秒被杀。基础设施事件，与代码无关 |
| `9b75b4f` / `e4522a1` / `8527785` / `13de95d` | **全部是 huggingface_hub 升级** |

**把它们当成同一个问题追，是走弯路的根本原因。** 有日志之后一次分类就看清了。

### 教训

- 拿不到日志时**不要靠改配置二分猜**：五次构建、四个假设全错（CUDA floor、
  SM 10.0、GHA 层缓存、多架构太重），真因不在任何一个里面
- Actions 日志 API 会 302 到 Azure blob，**重定向时不能带 `Authorization` 头**，
  否则 401。要手动处理重定向（`scratchpad/watch_ci.sh` 里有可用实现）
- 往一个精心 pin 过的基础镜像里 `pip install` 任何东西，都可能悄悄升级它的依赖

### 仍待验证（独立变量，别和别的改动混在一起）

**多架构编译从未真正完成过**——唯一跑到编译阶段的那次被 runner 杀了。
当前是 SM 12.0 单架构。想扩到 8.9（L40S、RTX 6000 Ada）和 9.0（H100、H200）
必须**单独一个提交**试，并同步更新 `gpu_policy.json`（测试会强制两者一致）。
SM 10.0（B200）永远不行：SageAttention v2.2.0 的 `SUPPORTED_ARCHS` 里没有它。

另一个已知脆弱点：Dockerfile 用 `pip install git+https://...` 装 SageAttention，
`git clone` 在受限网络会尝试交互式认证然后失败（复现 Pod 上实际发生过）。
改成装固定 commit 的 codeload tarball 可把 git 从构建路径去掉。

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

1. **确认 `d5dd9d5` 构建成功**（失败就再读日志，方法见第四节）
2. 用新 SHA 建 **volume-free 模板**
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
