# Model Profiles

本文档只记录经过真实验证的模型配置。未验证的候选值不会写入这里。

## `sdxl-mapchip-ipadapter` v1 — Verified 2026-08-02

真实冒烟机器：Windows + NVIDIA GeForce RTX 5080 Laptop GPU（16 GB VRAM），
驱动 610.88。该机器是 16 GB 显存开发机；**不要**把这些结果推广为 8 GB
设备的通用承诺。

### 组件（均为已验证的 pinned artifact，大小与 SHA-256 已锁定）

| 组件 | 版本/commit | 大小 | SHA-256 |
|---|---|---:|---|
| ComfyUI Windows NVIDIA portable | v0.29.2 / `322122449c9d2ba8b8df1bb517364527dd0615f1` | 2,103,175,457 | `e7a39a81…c6fc` |
| SDXL Base 1.0 | `462165984030d82259a11f4367a4eed129e94a7b` | 6,938,078,334 | `31e35c80…e5b` |
| mapchipLora | `7ff7d9e43c9c364eb25ca283851565b7c5778dbf` | 912,555,676 | `9a047fce…ac1ae` |
| IP-Adapter SDXL ViT-H | `018e402774aeeddd60609b4ecdb7e298259dc729` | 698,391,064 | `ebf05d91…831` |
| CLIP ViT-H image encoder | `018e402774aeeddd60609b4ecdb7e298259dc729` | 2,528,373,448 | `6ca9667d…b030` |
| ComfyUI IP-Adapter Plus | `a0f451a5113cf9becb0847b92884cb10cbdec0ef` | 306,422 | `c6c49c82…347` |

完整哈希见
`manifests/model-profiles/sdxl-mapchip-ipadapter-v1.json` 与
`manifests/runtimes/comfyui-windows-nvidia-v0.29.2.json`。

### 实测结果（2026-08-02，`tools/Invoke-Phase4Smoke.ps1`）

- 运行时安装：官方便携包解压至
  `runtime/comfyui/0.29.2-<manifest 前缀>/ComfyUI_windows_portable/`；
  归档根目录实测为 `ComfyUI_windows_portable`。
- 解压后端：官方 7z 使用 BCJ2 压缩，py7zr 无法解压；清单预检仍用 py7zr，
  解压使用 Windows 内置 bsdtar（`C:\Windows\System32\tar.exe`），
  参数固定、无 shell、先通过全部安全成员预检。
- 模型/自定义节点：四个模型文件复用本地已验证文件（无重新下载）；
  自定义节点 commit 归档下载并解压进受管运行时的
  `ComfyUI/custom_nodes/`。
- text2img 冒烟：completed，11.1 秒，输出 1024×1024 正方形 PNG。
- img2img 冒烟：completed，6.0 秒，输出 1024×1024 正方形 PNG
  （结构参考先经 ImageScale 放大到 1024）。
- 重启审计：受管进程 stop → start → stop 成功，就绪校验通过。
- 证据：`docs/evidence/phase-4/evidence.json`（已脱敏，无绝对路径）。

### 语义与已知限制

- IPAdapter preset 使用 `STANDARD (medium strength)`，对应文件
  `ip-adapter_sdxl_vit-h.safetensors`；没有使用 PLUS 变体。
- 多张风格参考通过多个 LoadImage + ImageBatch 链合并，`combine_embeds`
  固定为 `average`。
- `weight_type` 固定为 `style transfer`。
- 该节点（`cubiq/ComfyUI_IPAdapter_plus`）自 2025-04-14 起仅维护；
  commit 固定为 `a0f451a5…`，升级前必须重新验证。
- 本页数值仅代表上述固定配置；更换驱动、ComfyUI 或模型版本后
  必须重新冒烟。
