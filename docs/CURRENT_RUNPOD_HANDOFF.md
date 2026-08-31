# RunPod 双链路交接记录（2026-08-30）

## 当前目标

在相同硬件条件下对齐并比较两条独立视频链路：

- V1：PinkCherry LTX 2.3 v1.8
- V2：Wan 2.2 A14B FP8 + 必选成人 LoRA + AudioLDM2
- 两条链路统一使用 `NVIDIA RTX PRO 6000 Blackwell Server Edition`（96GB）
- GPU 单价硬上限 `$3.00/h`；当前 Secure Pod 目录价 `$2.09/h`
- Pod 仅在任务到达时创建，任务结束或 30 分钟超时后删除

## 已完成

- L40 48GB 实测在加载 Wan FP8 双专家和 UMT5 时 OOM，已排除。
- RTX PRO 6000 已完成 Wan 4 秒、480p、16:9 实际推理验证：
  - 40 步去噪约 `274.23s`
  - 解码约 `7.87s`
  - 含冷启动端到端约 `513.85s`
  - 峰值显存约 `44.1GB`
- Wan 与 LTX 已建立一次性 Pod 模板和镜像。
- Wan 已从常驻 Serverless 改为按任务创建的精确 GPU Pod，并有价格、GPU 型号、超时、回调后删除四层保护。
- Wan 已支持按顺序尝试多个“数据中心 + 对应网络卷”，当前顺序为 KS2 → NE1 → NC2。
- RunPod 控制面已从 REST v1 迁移到 REST v2；Serverless 作业 API 保持不变。
- 提供 `RUNPOD_API_V1=1` 紧急回滚分支，但 v1 不支持 US-NC-2。
- 测试结果：`52 passed`；迁移扫描器结果为 `Nothing to migrate`（仅保留显式回滚分支）。
- `VIDEO_UPLOAD_TOKEN` 已轮换并同步 Railway/RunPod 模板；不要在日志中输出变量值。

## US-NC-2 模型卷

- Wan：`nv7g5aobqn`，70GB，`papa-wan22-fp8-models-nc2`
- LTX：`mmw8n3z0t2`，100GB，`pinkcherry-ltx23-models-nc2`
- Wan 的 FP8 主模型、双 LoRA、AudioLDM2 已下载完成。
- LTX 实际占用约 74GB；PinkCherry、distilled LoRA、spatial upscaler 三个关键文件 SHA256 均通过校验，Gemma 五个分片已下载。
- 新增存储成本约 `$11.90/月`。

## 当前资源/费用状态

- 当前运行中 RunPod Pod：`0`
- 当前没有 GPU 小时费，仅网络卷存储费。
- 两个 NC2 下载 Pod 已在验证完成后删除。
- RTX PRO 6000 v2 目录最近一次查询：全局 HIGH、US-NC-2 MEDIUM、Secure `$2.09/h`。

## 最新根因与代码状态

第一次 NC2 提交失败有两个连续原因：

1. REST v1 的数据中心枚举不认识 `US-NC-2`，已迁移 REST v2 修复。
2. v2 将 `allowedCudaVersions=["13.0"]` 解释为精确匹配，而 NC2 当前机器上报 CUDA 13.2；已改为 `minCudaVersion="13.0"`。

CUDA 修复已推送到 `main`：

- `5c09d0b` — Use CUDA floor for RunPod v2 pods
- `1675a4c` — Test RunPod CUDA floor placement

注意：当前 Railway 最近成功部署 `15b34743-4494-4664-8a32-8230dc9861de` 早于上述两个 CUDA 修复提交；下个会话首先触发/确认部署最新 `1675a4c`。

## 下个会话的执行顺序

1. 触发 Railway 部署最新 `main`，确认健康检查成功，且生产变量为：
   - `RUNPOD_MANAGEMENT_API_BASE_URL=https://api.runpod.io/v2`
   - `RUNPOD_API_V1=0`
   - NC2 additional region volume 指向 `nv7g5aobqn`
2. 通过生产登录 API 提交 Wan 4 秒、480p、16:9、安全测试提示词。
3. 确认创建的是 NC2 的精确 RTX PRO 6000，价格 `$2.09/h`，任务成功回调后 Pod 自动删除。
4. 使用 LTX one-shot 模板和 NC2 卷 `mmw8n3z0t2`，在同一 RTX PRO 6000 上跑相同提示词、时长、尺寸和种子。
5. 记录两条链路的冷启动、模型加载、视频推理、音频推理、峰值显存、总耗时和实际成本。
6. 确认最终 RunPod Pod 列表为空，再把可比结果写入实验记录/UI 元数据。

## 已知模板/镜像

- Wan 模板：`wjxhc0dtid`
- Wan 镜像：`ghcr.io/ericstory/papa-wan-video:2d53dce230f68d475826b83c4924553c4ef67370`
- LTX 模板：`km9g8f4guq`
- LTX 镜像：`ghcr.io/ericstory/papa-ltx-video:87901bc5d36164d54f211b94efdc2f3165b8a2b8`

不要把 RunPod API key、Hugging Face token、Railway 管理员密码或上传 token 写入本文件或命令输出。
