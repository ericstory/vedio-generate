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

- Wan 模板：`wjxhc0dtid` → 镜像 `ed4470f19c328e98a13066d0366935887fe589a9`
- LTX 模板：`km9g8f4guq` → 镜像 `87901bc5d36164d54f211b94efdc2f3165b8a2b8`
- 两模板 registry 凭据：`cmtgxws1c003d14njrtc07zd2`

## 下一步建议

1. 用户人工验收两个视频（`/generate` 任务列表即可播放/下载；LTX one-shot 的视频不在
   任务 DB，仅在卷上：`d5ce95fd-c283-41ee-a7eb-248ccdabeeef.mp4`）。
2. 启动 `video-pipeline-v2.md` 的固定 A/B 矩阵（6–10 提示词 × 3 seed，人工盲评）。
3. 可选改进：Wan handler 拆分模型加载与纯推理计时；LTX one-shot 增加回调以纳入任务 DB。

不要把 RunPod API key、GitHub PAT、Railway 管理员密码或上传 token 写入本文件或命令输出。
