# 自建视频链路 V2：质量优先方案

更新时间：2026-08-30（America/Los_Angeles）

速度修订（2026-08-30）：旧版约 126GB BF16 + CPU offload 在 H100 上曾有一个 15 秒 720p
任务跑满两小时后失败，单任务约花 `$9.97`，因此不再作为生产方案。V2 改为 NVIDIA 官方
约 45GB ModelOpt FP8 权重 + SGLang 动态双 LoRA；V1/V2 下一轮统一在 US-KS-2 的 L40
48GB 上验证，Serverless Flex 约 `$1.908/小时`，硬上限 `$3/小时`。

## 结论

保留已经上线的 PinkCherry LTX 2.3 作为 V1，不原地替换。V2 建立一条完全独立的
Wan 2.2 A14B 质量链路，共用 Railway 的登录、任务列表和成品存储，但使用独立的
Worker 镜像、RunPod Serverless Endpoint、Network Volume、模型配置和成本熔断器。

首轮验证顺序：

1. 先用现有 V1 做 8/12/20 steps 的固定种子参数 A/B，不创建任何新基础设施。
2. V2 使用官方 `nvidia/Wan2.2-T2V-A14B-Diffusers-FP8`，支持 4–15 秒，先验证 480p，再验证 720p。
3. 成人 LoRA 是 V2 必选组件：高噪声与低噪声权重分别装入两个专家，不能由任务关闭。
4. 用户对 V1/V2 成片投赞成或反对票，以真实投票数据决定后续默认模型。

V2 的产品入口接受一段提示词，单次生成 4–15 秒 T2V 镜头，并以同一提示词生成环境声/音效。
15 秒长镜头已经进入产品能力，但复杂多阶段叙事仍建议拆镜头；AudioLDM2 生成的是氛围和音效，
不承诺对白口型同步。参考图增强留给后续 I2V 版本。

## V1 已记录基线

当前生产链路：

```text
浏览器 /generate
  → Railway FastAPI + SQLite + 管理员会话
  → RunPod Endpoint aoma1602mogius
  → PinkCherry LTX 2.3 v1.8 native two-stage Worker
  → Railway 受认证内部上传接口 + 持久卷
```

- 模型：PinkCherry v1.8 BF16 + LTX 2.3 distilled LoRA 0.6 + spatial upscaler + Gemma 3 12B。
- 推理：当前 20 steps，48GB MIG 使用 CPU offload，输出带同步音频。
- 成本：Endpoint 空闲保持 `workersMin=0, workersMax=0`；任务提交前短暂打开
  `workersMax=1`，终态后自动恢复为 0。
- 存储：US-NE-1 和 US-KS-2 各有一个 100GB 模型卷；两个卷不自动同步。
- 实测：4 秒 480p 约 203 秒；一次 15 秒 720p 约 589 秒。时间只是当前样本，不作为 SLA。

近期成片抽样显示，V1 的优势是单人近景、慢动作、脸部一致性和原生同步音频。主要问题不是
编码分辨率，而是复杂动作、多阶段叙事、多人身体接触、空间关系和运镜遵循；部分结果还出现
伪字幕、水印感、训练素材风格和重复运动。因此单纯从 480p 改到 720p，或盲目增加采样步数，
不会解决核心质量问题。

PinkCherry v1.8 发布者的 distill 工作流使用 8 steps、LoRA 0.6；当前服务的 20 steps 应与
8、12 steps 做同种子 A/B。该测试可能改善蒸馏路径匹配和成本，但预计不能弥补基础模型在
复杂物理交互上的能力上限。

## 模型判断

### 首选：官方 Wan 2.2 T2V-A14B

Wan 2.2 A14B 是高/低噪声双专家 MoE，总参数约 27B、每步激活约 14B。官方 T2V 模型支持
480p、720p 和固定 5 秒片段，并强调复杂运动和电影美学。它保持与 V1 相同的文生视频输入，
适合直接进行模型投票对比。

### 成人适配器是强制模型层

`lopi999/Wan2.2-I2V_General-NSFW-LoRA` 的 HF 镜像包含高噪声和低噪声两个权重，合计约
1.23GB。原发布者说明权重可加载到 14B T2V 或 I2V；V2 每次推理都同时启用高噪声与低噪声
权重，默认强度 0.9。发布者也明确称其仍属实验版本，因此质量结论来自用户投票，而不是下载量
本身。Worker 缺少任一权重都拒绝启动，API 不提供关闭适配器的参数。

`WAN2.2-14B-Rapid-AllInOne` 及其 NSFW GGUF 合并不作为 V2 首选：维护者已标记 deprecated，
默认目标是 1 CFG、4 steps 的速度与易用性，并承认完整 Wan 2.2 双模型能给出更高质量。
它可在以后作为低成本实验项，但不属于质量优先链路。

### 暂不选 LTX 2.5 或 HunyuanVideo 1.5

- LTX 2.5 是值得单独评估的 V1.5：保留 LTX 的同步音视频与现有工程复用优势，但 PinkCherry
  是 LTX 2.3 完整微调权重，不应假设可无损迁移到 2.5。它不能直接证明能修复当前成人动作质量。
- HunyuanVideo 1.5 的 8.3B、低显存与 480p/720p 支持很适合成本线，但当前缺少同等成熟、
  可验证的成人适配器证据。它应排在 Wan A/B 之后。

## V2 独立架构

```text
统一 UI /generate
  → Railway control plane
      ├─ seedance-*              → BytePlus（现有）
      ├─ pinkcherry-ltx-v1       → RunPod LTX Endpoint（现有）
      └─ wan-quality-v2          → RunPod Wan Endpoint（新增、独立）
                                    ├─ Wan 2.2 T2V-A14B 双专家
                                    ├─ mandatory adult high/low LoRA
                                    ├─ AudioLDM2 环境声/音效
                                    └─ AAC mux/encode → 受认证内部上传接口
```

隔离要求：

- 新目录 `workers/wan-video/`，不修改 V1 Worker 的模型或依赖。
- 新的 `RUNPOD_WAN_ENDPOINT_ID` 和 Wan 专属成本熔断器；V1、V2 各自最多一个 worker。
- 新 Endpoint 保持 `workersMin=0`，空闲时不产生 GPU 费用。
- 新模型卷不复用现有 100GB PinkCherry 卷。
- 任务记录必须保存 provider、模型 revision、adapter revision、seed、steps、尺寸、帧数、
  推理秒数和 GPU 类型，才能复现和比较。
- 音频是独立后处理模型，画面仍由 Wan 决定；任务可关闭音效以缩短生成时间。

## 存储与 GPU

通过 Hugging Face API 按仓库文件大小汇总：

- NVIDIA Wan 2.2 T2V-A14B ModelOpt FP8：约 45.02GB。
- 强制成人高/低噪声 LoRA：约 1.23GB。
- AudioLDM2 safetensors：约 4.48GB。

因此 V2 新卷使用 70GB，给约 50.73GB 模型及 Hub 元数据保留空间。旧 150GB BF16 卷在
FP8 实机验收完成前保留，验收后再由所有者确认删除。

2026-08-30 的只读 RunPod 库存与官方价格核验：

- L40 48GB：Secure Pod 标价约 `$0.82/h`，US-KS-2 库存 Low。
- ADA_48_PRO Serverless Flex 价约 `$1.908/h`；通过排除 L40S 与 RTX 6000 Ada，把两条
  Endpoint 都固定为 L40。Serverless 与 Pod 标价不是同一计费档。

Queue-based Serverless 没有 BLACKWELL_48/MIG48 pool；Pod 的 MIG48 精确 GPU ID 不能直接
用于 Endpoint。V1 和 V2 因此都固定为同一个 L40 精确 GPU ID，不配置 A100/H100/L40S 回退，
避免投票混入硬件差异。实际创建卷或修改 Endpoint 前必须重新读取目标数据中心的逐区域库存和
Serverless 价格，任何实际执行价超过 `$3/h` 都停止。Network Volume 会把 Endpoint 约束到对应区域。

付费边界：代码、镜像和模型锁先完成；测试 Endpoint 空闲 `workersMin=0`。只在实测窗口把
`workersMax` 设为 1，并用 30 分钟 execution timeout 限制首轮损失，任务终态立即缩回 0。

## 固定 A/B 验收

建立 6–10 个固定提示词与固定 seed 的私有测试集，覆盖：

- 单人近景慢动作；
- 单人全身大幅动作；
- 两位虚构成年人接触与遮挡；
- 镜头平移或环绕；
- 人物身份和面部一致性；
- 三个连续 3–5 秒镜头的拼接一致性。

每个候选至少跑 3 个 seed，人工盲评分：提示词遵循、人体结构、时间一致性、运动幅度、
脸部一致性、镜头稳定、伪字幕/水印、生成时间和实际 GPU 成本。V2 只有在关键质量项明显
优于 V1，且失败率可接受时才进入 UI 默认选项；不能以单个“最好样片”决策。

建议第一轮矩阵：

| 组别 | 模型 | 设置 | 目的 |
| --- | --- | --- | --- |
| A1 | PinkCherry LTX 2.3 | 8 steps / 固定 seed | 对齐发布者 distill 工作流 |
| A2 | PinkCherry LTX 2.3 | 12 steps / 同 seed | 找质量与成本中点 |
| A3 | PinkCherry LTX 2.3 | 20 steps / 同 seed | 当前生产基线 |
| B1 | Wan 2.2 T2V-A14B + 强制成人 LoRA | 480p / 5 秒 / 同 seed 组 | V2 质量基线 |
| B2 | Wan 2.2 T2V-A14B + 强制成人 LoRA | 720p / 5 秒 / 同 seed 组 | 验证分辨率代价 |

## 实施与付费边界

不付费即可完成：

1. 添加 Wan provider 接口、配置、数据库字段和 UI 模型项，但用 feature flag 隐藏。
2. 创建 `workers/wan-video/` 原生 Python Worker、锁定 revision、单元测试和 Docker 构建。
3. 实现固定测试集、评分表、视频元数据采集和成本统计。
4. 本地完成镜像构建并推送不可变 SHA tag。

以下动作开始或增加费用，执行前必须由用户确认：

1. 创建 150GB Wan Network Volume。
2. 启动临时 CPU/GPU Pod 下载模型。
3. 创建或第一次调用 Wan Serverless Endpoint。
4. 运行任何真实 A/B 视频任务。

首轮只建立一个区域、一个卷、一个 Endpoint，`workersMin=0`、`workersMax=1`、execution
timeout 30 分钟。质量通过后再讨论第二区域冗余；两个 Network Volume 不会自动同步模型。

## 参考来源

- Wan 2.2 官方 T2V 模型卡：https://huggingface.co/Wan-AI/Wan2.2-T2V-A14B
- Wan 2.2 官方 Diffusers T2V：https://huggingface.co/Wan-AI/Wan2.2-T2V-A14B-Diffusers
- NVIDIA Wan 2.2 ModelOpt FP8：https://huggingface.co/nvidia/Wan2.2-T2V-A14B-Diffusers-FP8
- Wan 2.2 官方代码：https://github.com/Wan-Video/Wan2.2
- PinkCherry LTX 2.3 v1.8：https://huggingface.co/SexGod1979/PinkCherry_NSFW_LTX23
- 候选 Wan 成人 LoRA：https://huggingface.co/lopi999/Wan2.2-I2V_General-NSFW-LoRA
- Rapid All-in-One 发布页：https://huggingface.co/Phr00t/WAN2.2-14B-Rapid-AllInOne
- LTX 2.5 官方模型卡：https://huggingface.co/Lightricks/LTX-2.5
- HunyuanVideo 1.5 官方模型卡：https://huggingface.co/tencent/HunyuanVideo-1.5

内容边界沿用 V1：仅限合规的虚构成年人内容，拒绝未成年人、非自愿、兽交/乱伦和真人
无授权色情深伪。
