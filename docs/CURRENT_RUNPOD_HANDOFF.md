# RunPod 链路交接记录（2026-09-03 更新）

## 当前状态一句话

**MiniMax H3 + PinkCherry 主线已经两次真实出片成功**（第八次 05:14Z、第九次 07:06Z），
1344×768、5 秒、24fps、32kHz 立体声，走生产 API 全流程。
**第九次是新形态**：web 提交 0.65 秒返回，守卫循环 5.1 秒拿到 Pod，
提交到成片 482 秒；模板 `jkrh512m3s` 用最新镜像 `5a869e1`、**不带任何 env 覆盖**，
峰值显存 45,616 MB 与第八次完全一致（代码默认值 = 模板覆盖，5c 验毕）；
超时守卫已打开（`H3_SECONDS_PER_MPIXEL_STEP=0.1`，投影 111.5 秒 vs 实测 108.5 秒）。

前七次失败全部是 sglang 加载契约的细节（第十二节逐条记录，每条都已修进代码）。
web 端「点一次就稳」靠的是第十三节的入队 + 守卫循环申请 GPU（提交 `79dec8f`）。

**下一步：第九节第 5b（显存余量调常驻层数）或第 6 步（保温）起。**

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

2026-09-03 现役模板 `RUNPOD_H3_POD_TEMPLATE_ID=jkrh512m3s`（见第十三节）；
`RUNPOD_COST_GUARD_ENABLED=1` **现在是硬依赖**——守卫循环负责给排队任务申请 Pod，
关掉它 web 端的 H3/Wan 任务会退回到请求内同步建 Pod 的老路径。
2026-09-02 已切到 volume-free：
`RUNPOD_H3_POD_NETWORK_VOLUME_ID` **已删除**（Railway CLI 不接受空值，
未设置在代码里等价于空）；`RUNPOD_H3_POD_ADDITIONAL_GPU_IDS` 是 `gpu_policy.json`
第 2–5 位（PRO 6000 Workstation、PRO 5000、5090、PRO 4500）。
`RUNPOD_H3_POD_DATA_CENTER_ID` / `FALLBACK_*` 还留着，但卷为空时代码不读它们。
部署 `e654ad7c`（07:27 PT）SUCCESS，容器日志显示 Uvicorn 正常启动，公网 healthz 200。

**RunPod**：

| 资源 | ID | 备注 |
| --- | --- | --- |
| H3 卷（主） | `n7meo4oft2` | US-NC-2 170GB，已灌满 145,443,261,098 字节 |
| H3 卷（备） | `qextiwmyla` | US-KS-2 170GB，已灌满 145,443,261,107 字节 |
| **H3 模板（现役）** | `jkrh512m3s` | volume-free，`5a869e1` 镜像，220GB 容器盘，**无 `H3_EXTRA_SERVER_ARGS_JSON`、无 `H3_LORA_MERGE_MODE`**（全靠代码默认值），`H3_SECONDS_PER_MPIXEL_STEP=0.1` |
| H3 模板（上一版） | `02hdqp64b1` | `627dded` 镜像 + env 覆盖，第八次成功用的；`jkrh512m3s` 验过后可删 |
| H3 模板（更早） | `5hwjktbaa2` `sx4pndyt2r` `kdf3q6n3i4` `v3waitxptu` `7xnj6d52vv` `lpalzoy70e` `z2jlkzb9bt` | 迭代残留，可删 |
| LTX 模板 | `km9g8f4guq` | |
| Wan 模板 | `wjxhc0dtid` | |
| GHCR 凭据 | `cmtgxws1c003d14njrtc07zd2` | `read:packages` classic PAT |

**当前运行中 Pod：0。** 所有临时/探测 Pod 均已删除。

⚠️ **9 个卷 / 1000GB ≈ $70/月，7×24 计费。** volume-free 跑通后按下面"待清理"处理。

---

## 四、✅ 已确诊并修复：H3 镜像 CI 构建失败

**2026-09-02 已确认修复有效**：Actions run `33639454654` 成功，全程 13 分钟；
SageAttention 2.2.0 在 SM 12.0 上编译 386 秒，`import sageattention` 通过，
镜像 `ghcr.io/ericstory/papa-minimax-h3:d5dd9d58f306769c0dceff687773ea7d00bbfbcf`
已推送（GHCR 包**可匿名拉取**，查 tag 不需要 token）。

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

1. ~~确认 `d5dd9d5` 构建成功~~ ✅ 2026-09-02，见第四节
2. ~~用新 SHA 建 volume-free 模板~~ ✅ `5hwjktbaa2`
3. ~~Railway 变量 + 部署~~ ✅ `e654ad7c`
4. ~~跑第一次真实出片~~ ✅ 2026-09-03 05:14Z 成功（第八次，第十二节）
5. ~~用实测填 `H3_SECONDS_PER_MPIXEL_STEP` 打开超时守卫~~ ✅ 2026-09-03 模板 `jkrh512m3s`
   填 `0.1`（实测 108.4 秒 ÷（1.032 MP × 120 帧 × 9 步）= 0.097 s/(MP·帧·步)；
   768p 15 秒 360 帧投影 324 秒，远在 1500 秒预算内）
5b. 显存还有 50GB 余量（峰值 45.6 / 95），`dit_layerwise_resident_layers` 可以从 25 往 40 提，
   每提一层少流一层权重；每次改完看返回的 `peak_memory_mb`。**没动**，
   留给下一轮单独调（和 5c 混在一起就分不清是谁的功劳）
5c. ~~用最新 SHA 的镜像建一个不带 `H3_EXTRA_SERVER_ARGS_JSON` 的模板验一次~~ ✅ 见第十三节
5d. ~~web 端稳定创建任务：提交先入队、守卫循环申请 GPU~~ ✅ 提交 `79dec8f`，见第十三节
6. 保温（拉取式 worker + 自动空闲超时）
7. 10Eros Max AdaLN 离线还原 → 私有仓库 → UI 两个能力
8. 清理：删 9 个卷（省 $70/月）、闲置模板（迭代留下的 `5hwjktbaa2`、`sx4pndyt2r`、
   `kdf3q6n3i4`、`v3waitxptu`、`7xnj6d52vv`、`lpalzoy70e`、`02hdqp64b1` 都可删，**现役是 `jkrh512m3s`**）、
   5 个残留 serverless endpoint。
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
- Railway CLI `variable set` **不接受空值**（`KEY=` 报 Invalid variable format）；
  要清空就 `variable delete`，它没有 `--skip-deploys`，会触发一次旧镜像重部署，
  紧接着 `railway up --detach` 覆盖即可
- `railway up` 部署的是本地目录，不是 git；部署前确认 `git status` 干净
- RunPod `GET /v2/pods/{id}/logs` 是 **SSE 流**（`tail=5000&source=container`），
  不能一次性 `read()`，要按行读、靠 socket 超时收尾；`repro_pod.py` 有可用实现
- 一次性 Pod 失败后日志随 Pod 一起没了。要抓子进程 traceback，别再起复现 Pod：
  `smoke.py` 现在把容器日志尾部附进失败回调的 `error` 里

## 十一、2026-09-02 本机访问 Railway 被阻断（第一次出片因此没跑）

**现象**：`curl` / Python urllib / `nc` 到 `69.46.46.34:443`（Railway 边缘，
`RLWY-HIKARI-01`）全部连接超时；`railway.com`、另一个 Railway 服务
`aigirl-server-production.up.railway.app`（`69.46.46.59`）同样不通；traceroute
过 Cogent（`154.54.167.x`）之后全是 `*`。GitHub、RunPod API、hackertarget 都正常。
无系统代理，无 IPv6。

**公网视角正常**：`api.hackertarget.com/httpheaders` 探 healthz 得
`HTTP/1.1 200 OK`，`x-railway-edge: iad1`。所以 RunPod Pod 的回调和上传不受影响，
只是从这台 Mac mini 提交不了任务。

**试过且不通的绕路**（下次别再花时间）：
- WebFetch 沙箱对该网段返回 `ECONNREFUSED`——它自己拦的，**不能当外部视角用**
- `railway ssh -- …`（在容器内打 127.0.0.1:8000）、SSH 到 MacBook 借出口：
  都被 Claude Code 自动模式的分类器拦截，需要用户手动放行或自己跑
- Chrome 扩展未连接
- tailnet 有出口节点（`office-mesh-prod-us-node-pve-0624`），切出口节点是
  全机网络变更，未擅自动

**恢复后直接做**：`python3 smoke_submit.py`（本会话 scratchpad：
`/private/tmp/claude-501/-Users-macmini-workspace-papa/871d013a-9993-4f9a-8dcf-3e70de00eb1b/scratchpad/`，
同目录还有 `mktemplate.py`、`pods.py`；scratchpad 是临时目录，可能被清）。

## 十二、第一次真实出片（2026-09-03 02:13Z）：链路全通，倒在 model_path

**验证通过的**（Pod `m8sp9v8w1x3ors`，RTX PRO 6000 Server，$2.09/h，US-NC-1）：

- volume-free 放置：落在 **US-NC-1**，一个我们从没放过卷的机房，第一次尝试就成功
- 开机下权重：`model_download_start` → `model_download_done` 在 4 分钟内完成
  （建 Pod 02:13:52 → `model_load_start` 02:18:00，含拉镜像）
- 进度回调 3 次、结果回调 1 次全部 200；结果回调后控制面**立刻删 Pod**（存活 234 秒）
- 全程费用约 $0.14

**失败**：`EOFError: `（空消息），发生在 `model_load_start` 后 22 秒。空消息是因为
sglang 的调度器在 spawn 的子进程里加载模型，子进程崩了父进程只从管道拿到裸 EOFError。
起了一个不带回调的复现 Pod（`rti6qrqvvibt4c`，322 秒，$0.19）读容器日志才拿到真因：

```
ValueError: Model directory /models/MiniMax-H3-PinkCherry/FL2VA/FL2VA does not contain model_index.json
```

sglang v0.5.18 的 `MiniMaxH3Pipeline` **自己**把 `model_variant=fl2va` 映射成子目录 `FL2VA`
再拼到 `model_path` 后面（`default_model_subfolder = "FL2VA"`，`_load_config` 里强制
`model_subfolder`）。我们把 `model_path` 指到分区目录，就成了 `FL2VA/FL2VA`。

**修复**（本次提交）：`model_path` 改为快照根 `/models/MiniMax-H3-PinkCherry`，并显式传
`pipeline_class_name="MiniMaxH3Pipeline"`——因为 HF 仓库根目录的 `model_index.json` 声明的
是 diffusers 的 `MiniMaxH3ModularPipeline`，sglang 注册表里没有，靠它选类会失败；
sglang 只在 `KNOWN_NON_DIFFUSERS_DIFFUSION_MODEL_PATTERNS` 里按路径含 `minimaxai/minimax-h3`
匹配，本地路径不含所以也走不到。`H3_BASE_MODEL_ROOT` 仍指分区目录，只用来检查文件是否齐；
`handler` 会校验它的目录名必须是 `FL2VA`。

顺手：`smoke.py` 用 `tee` 把整个进程树的 stdout/stderr 镜像到 `/tmp/h3-worker.log`，
失败回调的 `error` 末尾附最后 3000 字符，以后子进程崩溃不用再起复现 Pod。

### 第二次真实出片（2026-09-03 02:59Z，Pod `qw6mhpjixazetg`，US-KS-2）

`model_path` 修复生效，加载走到了组件层；新的日志尾部机制也生效，失败回调直接带回
traceback，没再起复现 Pod。这次的失败：

```
ValueError: The repository for /models/MiniMax-H3-PinkCherry/FL2VA/audio_vae contains custom code.
Pass `trust_remote_code=True` to allow loading remote code modules.
```

FL2VA 分区的 VAE / 文本编码器是自定义代码模块（镜像里 diffusers 0.32.2 没有 H3 类），
sglang 的组件加载器经 diffusers `AutoModel.from_pretrained` 加载，必须 `trust_remote_code=True`。
快照 revision 固定，信任的是审过的那份代码。

顺手加了 `H3_EXTRA_SERVER_ARGS_JSON`（模板 env，JSON 对象），最后合并进
`DiffGenerator.from_pretrained` 的参数并覆盖内置值——**以后加载参数级的调整只改模板，
不用再等 13 分钟镜像构建**。

另一个观察：US-KS-2 上容器 9 分钟才起来（拉 14GB 镜像），US-NC-1 只要约 1 分钟。
30 分钟守卫下这仍够用，但保温方案（第七节）的价值又高了一截。

### 第三次真实出片（2026-09-03 03:39Z，Pod `mzjgz8e9in8gcw`，US-NE-1）：坏主机

`trust_remote_code` 修复后本应进入加载，但这台主机从头就不对劲：145GB 下载花了
**974 秒**（PinkCherry 单文件 441 秒；前两次整套 3 分钟），随后 worker 在父进程第一次
调 CUDA 就死：

```
RuntimeError: CUDA unknown error - this may be due to an incorrectly set up environment ...
```

sglang 一行都没输出。同一镜像在 US-NC-1 和 US-KS-2 都顺利过了这一步，判定为 RunPod
主机故障。Pod 存活 1149 秒，约 $0.67，全部浪费。

**应对**（本次提交）：`handler` 在下载权重**之前**先 `torch.cuda.init()` 探一次（3 次、间隔
10 秒），坏主机 30 秒内以 `GPU host unhealthy: …` 失败退回，进度阶段 `gpu_probe` 会带回
GPU 名。后续可让控制面对这类错误自动换主机重试一次。

### 第四次真实出片（2026-09-03 04:02Z，Pod `hzhqd4gbtidohl`，US-NC-1）：目录名

6 个组件全部加载完成（"Loading required modules 6/6"），倒在组装阶段：

```
AttributeError: 'DiffusersTI2VPipelineConfig' object has no attribute 'audio_vae_config'
```

sglang `registry.get_model_info` 判定"是不是原生 H3"的第一步是 `get_non_diffusers_pipeline_name`：
**目录 basename 必须等于 `MiniMax-H3`**（对照 `KNOWN_NON_DIFFUSERS_DIFFUSION_MODEL_PATTERNS` 里的
`minimaxai/minimax-h3`）。不匹配就去读根 `model_index.json`，读不到就退到 diffusers 通用配置，
H3 的 `MiniMaxH3PipelineConfig` 只被借走一个 `task_type`。`model_id` 参数在这条链上帮不上忙
（它只影响 `_get_config_info`，而 `get_model_info` 早已进了 diffusers 分支）。

**修复**：模型根改为 `/models/MiniMaxAI/MiniMax-H3`（镜像 HF 仓库 id），全部通过模板 env
生效（`MODEL_ROOT`、`H3_BASE_MODEL_ROOT`、两个权重路径），**没有重建镜像**；代码默认值同步改，
并加守卫：根目录名不是 `MiniMax-H3` 直接报错。新模板 `v3waitxptu`（仍是 `627dded` 镜像）。

到此为止 sglang 的加载契约总结：`model_path=<…>/MiniMaxAI/MiniMax-H3`（根，不是分区）+
`model_variant=fl2va` + `pipeline_class_name=MiniMaxH3Pipeline` + `trust_remote_code=True`。

### 第五次真实出片（2026-09-03 04:14Z，Pod `gt6qi0b1s8oxh2`，US-NC-1）：注意力后端

目录名修复后 sglang 正确选中原生 H3 配置，倒在加载文本编码器：

```
ValueError: Attention backend 'sage_attn' is not supported by this attention layer; supported backends: ['fa', 'torch_sdpa']
RuntimeError: Failed to load customized text_encoder; native fallback is disabled for this component configuration.
```

`attention_backend` 是全局的；Qwen3-VL 编码器和 H3 audio VAE 的注意力层只接受 `fa`/`torch_sdpa`
（DiT 没有限制，video VAE 也没有）。sglang 只对 LTX2 自动把 text_encoder 设成 torch_sdpa。
**修复**：`component_attention_backends={"text_encoder":"torch_sdpa","audio_vae":"torch_sdpa","video_vae":"torch_sdpa"}`，
DiT 保持 `sage_attn`。先经模板 `H3_EXTRA_SERVER_ARGS_JSON` 注入跑第六次，代码默认值同步提交。

### 第六次真实出片（2026-09-03 04:37Z，Pod `ln29qtkefc7ahg`，US-PA-1）：LoRA 合并

**全部组件、PinkCherry 权重、FP8 量化都加载成功，调度器起来了**，倒在最后一步 turbo LoRA：

```
RuntimeError: Failed to set LoRA adapter: The size of tensor a (21504) must match the size of tensor b (5376) at non-singleton dimension 1
```

`lora_merge_mode=auto` 对非 FSDP 权重一律合并进底层权重（`_merge_lora_into_data` 直接
`data.add_(B@A)`），而在线 FP8 量化后的权重是转置布局（`qkv_proj` 变成 [5376, 21504]），
合并代码没有处理。**修复**：`H3_LORA_MERGE_MODE=dynamic`——LoRA 作为运行时增量叠加在
`quant_method.apply()` 之后，与量化方法无关。先经模板跑第七次，代码默认值同步改。

### 第七次真实出片（2026-09-03 04:51Z，Pod `gx93uotdfjnmp3`，US-PA-1）：OOM

**模型、FP8、PinkCherry、动态 LoRA 全部就位，`video_start` 已回调，进入推理**，然后：

```
CUDA out of memory. Tried to allocate 250.00 MiB. GPU 0 has a total capacity of 94.97 GiB ... 89.06 GiB is allocated by PyTorch
```

账算得过来：在线 FP8 只量化线性层，PinkCherry DiT 常驻约 47GB（其中 26GB 是 bf16 的 AdaLN）；
video VAE 按 sglang 配置 fp32 常驻约 21GB；`text_encoder_cpu_offload` 走 FSDP CPU offload，
前向时 all-gather 到 GPU。sglang 自己的 H3 部署配置写明 `keep_resident_min_available_gb=120`，
参考部署是 4 卡——**单张 96GB 本来就不该全常驻**，handler 里的 96GB "speed" 档是拍脑袋的。

**修复**：三个组件组（`dit`、`text_encoder`、`vae`）全部逐层流式，DiT 保留 25 层常驻
（50 层，约 0.95GB/层），预取 2 层，`performance_mode=memory`。注意 sglang 的 `from_kwargs`
把**传入的每个键**都记为显式设置（值为 null 也算），所以想让它 auto 决策不能靠 JSON 传 null；
显式 `layerwise_offload_components` 的优先级高于旧的 `*_cpu_offload` 开关，直接给方案最稳。
先经模板跑第八次，代码里三个档位同步改（48GB/32GB 档是按比例缩的猜测，未实测）。

### 第八次真实出片（2026-09-03 05:05Z，Pod `mz8igw8nan53lw`，US-NC-1）：✅ 成功

模板 `02hdqp64b1`（镜像 `627dded` + env 覆盖：`H3_LORA_MERGE_MODE=dynamic`、
`H3_EXTRA_SERVER_ARGS_JSON` 含 SDPA 组件覆盖和逐层卸载方案）。

| 阶段 | 实测 |
| --- | --- |
| 建 Pod → 容器起来 | 约 1 分钟（US-NC-1，镜像有缓存） |
| 下权重 145GB | 113 秒 |
| 模型加载（含 FP8、PinkCherry、动态 LoRA） | 133 秒 |
| 推理 1344×768 × 120 帧 × 9 步 + 音频 | **108 秒** |
| 上传 | 0.2 秒 |
| Pod 存活（含控制面删除延迟） | 504 秒 ≈ $0.29 |
| 峰值显存 | 45.6 GB / 95 GB |

输出 `/generate/media/611c224c-690d-4c58-97d1-d779f54240fb.mp4`，seed 2083542369，
`verified_configuration=true`，有音频。元数据里 `performance_mode` 显示 `speed` 是旧镜像
handler 的报告字段，实际生效的是模板覆盖的 `memory`；新代码报告值已对齐。

**八次的总账**：GPU 费用约 $2.5，其中真正浪费的是坏主机那次 $0.67。

### sglang v0.5.18 加载 MiniMax H3 的完整契约（本次踩出来的）

1. `model_path` = 快照根，目录名必须是 `MiniMax-H3`（例：`/models/MiniMaxAI/MiniMax-H3`），
   `model_variant="fl2va"`，`pipeline_class_name="MiniMaxH3Pipeline"`
2. `trust_remote_code=True`（FL2VA 的 VAE/编码器是自定义代码）
3. `component_attention_backends={"text_encoder":"torch_sdpa","audio_vae":"torch_sdpa","video_vae":"torch_sdpa"}`，
   DiT 用 `sage_attn`
4. `quantization="fp8"` 时 LoRA 必须 `merge_mode="dynamic"`
5. 单卡 96GB 不能全常驻：`layerwise_offload_components=["dit","text_encoder","vae"]`，
   `dit_layerwise_resident_layers=25`（可上调），`dit_offload_prefetch_size=2`，`performance_mode="memory"`
6. 传给 `from_kwargs` 的每个键都算显式设置（含 null），别指望 auto

---

## 十三、2026-09-03：web 端稳定创建任务（提交 `79dec8f`）

**问题**：浏览器点「开始生成」时，控制面在 HTTP 请求里同步跟 RunPod 抢容量
（3 轮 × 5 种 GPU = 15 次 POST，中间两次 5 秒 sleep，全部跑在事件循环上），
抢不到就给用户一个 503「云 GPU 当前无可用机器」，用户只能再点一次。
RTX PRO 6000 secure 库存薄到一次尝试经常输（上一会话的 `smoke_submit.py`
要重试 40 次才敢说稳）。这段时间 healthz、任务列表轮询、Pod 回调全部被阻塞。

**改法（Pod 链路 H3 和 Wan 都适用）**：

- `POST /api/tasks` 对 Pod 链路只**落库**（`status=queued`、`provider_task_id=''`、
  `progress.stage=awaiting_gpu`）就返回 201，实测 0.65 秒。
- 已有的成本守卫循环（每 8 秒一 tick，`asyncio.to_thread` 里跑）多了一步
  `_acquire_pending_pods`：给每个排队任务做**一轮** lane sweep（`capacity_retry_sweeps=1`），
  最快 `RUNPOD_*_POD_ACQUIRE_RETRY_SECONDS`（默认 20 秒）一次，
  超过 `RUNPOD_*_POD_ACQUIRE_TIMEOUT_SECONDS`（默认 900 秒）就把任务置为 failed，
  错误文案明确写「本次未创建 Pod，不产生费用」。容量拒绝和 API 抖动
  （无状态码 / 429 / 5xx）都继续等；401、价格超上限这类不会自愈的错误立即失败。
- 拿到 Pod 后 `provider_metadata` 记 `pod_created_at`、`gpu_wait_seconds`、
  `gpu_acquire_attempts`；**30 分钟运行上限从 `pod_created_at` 起算**，不再从点击起算
  （否则排队 10 分钟的任务只剩 20 分钟给 Pod）。
- 任务表新增 `generate_audio` 列（守卫循环重放提交时要用）；
  启动时那句 `provider_task_id='' → id` 的回填**只对非 Pod 链路生效**，
  否则重启一次排队任务就会被当成「Pod 不见了」标成 expired。
- 任务列表接口不再去查还没有 Pod 的任务，其余 provider 轮询和同步提交
  （Seedance、LTX serverless）也挪到线程里，事件循环不再被阻塞。
- 前端：H3 归入 `selfHostedModels`（参考图置灰、1080p 禁用），768p 只在 H3 可选且为默认；
  新增阶段文案 `awaiting_gpu`（带已申请次数）、`pod_created`、`gpu_probe`、
  `model_download_start/done`；`model_load_done` 改成「模型就绪，视频推理中」
  （worker 在推理期间不发阶段回调，旧文案会让人以为卡住）。

**边界**：`RUNPOD_COST_GUARD_ENABLED=1` 现在是硬依赖——守卫循环就是申请器；
关掉它 `create_task` 会退回请求内同步建 Pod 的老路径（测试里默认就是这个模式）。
容器重启不丢排队任务（状态在 SQLite 里，守卫循环重启后接着申请）。
一个 Pod 链路同时只允许一个未完成任务，排队中的也算，第二次提交仍是 429。

**验证**：98 个测试全过（新增 8 个：入队不碰 provider、守卫循环拿 Pod、
容量拒绝持续等待并超时失败、非瞬时错误立即失败、运行上限按 Pod 创建时间算、
重启不回填排队任务、前端 H3 联动、单轮 sweep 选项）。
Railway 部署 `a9e92219`（2026-09-02 23:57 PT）。浏览器里确认：任务列表模型名正确、
详情页显示「云 GPU 已分配，正在启动」等阶段文案、切到 H3 时清晰度自动 768p、
参考图置灰、音效开关可用（Chrome 扩展在最后一步断线，没拿到「已完成」的截图）。

### 第九次真实出片（2026-09-03 06:58Z，Pod `wga7opfmwd53cy`，US-NC-1）：✅ 成功

任务 `440b7e0d`，模板 `jkrh512m3s`（镜像 `5a869e1`，无 `H3_EXTRA_SERVER_ARGS_JSON`、
无 `H3_LORA_MERGE_MODE`，`H3_SECONDS_PER_MPIXEL_STEP=0.1`）。通过生产 API 提交
（`smoke_submit.py`，和网页表单打同一个接口）。

| 阶段 | 实测 |
| --- | --- |
| `POST /api/tasks` 返回 | **0.65 秒**（status=queued，无 Pod） |
| 守卫循环拿到 Pod | 提交后 5.1 秒，第 1 次申请即成功 |
| 建 Pod → 容器起来 | 约 2.5 分钟（US-NC-1，新镜像没缓存，要拉 14GB） |
| 下权重 145GB | 77.8 秒 |
| 模型加载（FP8、PinkCherry、动态 LoRA） | 134.0 秒 |
| 推理 1344×768 × 120 帧 × 9 步 + 音频 | **108.5 秒**（守卫投影 111.5 秒，高 3%） |
| 上传 | 0.2 秒 |
| 提交 → 成片 | **482 秒** |
| 峰值显存 | **45,616 MB**（第八次 45.6 GB，完全一致） |

输出 `/generate/media/abd10ab2-3ff4-4b76-9529-ac4b7e771271.mp4`，seed 4086825481，
`verified_configuration=true`，有音频。结果回调后控制面立刻删 Pod，RunPod 当前运行中 Pod：0。
元数据新增 `pod_created_at`、`gpu_wait_seconds=5.1`、`gpu_acquire_attempts=1`、
`projected_denoise_seconds=111.5`。

**没验到的**：容量拒绝的排队路径（这次第一次就拿到了 Pod）。单元测试覆盖了它，
线上要等真的缺货才看得到「正在申请云 GPU 机器（已申请 n 次）」。
