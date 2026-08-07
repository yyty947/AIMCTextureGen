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

## `sdxl-mapchip-ipadapter` v2 — Verified 2026-08-07

v2 保留 v1 的 pinned runtime、模型、自定义节点和许可证摘要，只新增四个
版本化 workflow 变体；v1 manifest/workflow bytes 由自动化 SHA-256 回归保护。
v2 的 canonical manifest SHA-256 为
`c4be14ba2ff5cffc6c9ec603a2e58e200cc2952e1a249a05dc61ff7276d00f3a`，runtime
manifest SHA-256 为
`5d5fe88aaadd6004f398b6118a5d9e8ce4a350f6e37438964a4ea6cc2c5e4e10`。

### 实测资格门禁

- 机器：Windows x86_64，NVIDIA GeForce RTX 5080 Laptop GPU，驱动 610.88，
  16,303 MiB VRAM。
- 受管 ComfyUI：0.29.2；runtime commit
  `322122449c9d2ba8b8df1bb517364527dd0615f1`；内置 Python 3.13.14；
  PyTorch 2.13.0+cu130。
- `text2img-no-style`、`text2img-style`、`img2img-no-style`、
  `img2img-style` 均在 native batch 1/2/4 完成，共 12 个 cell；每个 cell
  产生 4 个输出，全部通过 1024×1024 RGB PNG 校验并完成确定性后处理。
- 实测 cell elapsed 为 21.188–26.468 秒；peak VRAM 为 6,574–8,535 MiB，
  process RAM 为 2,604–3,661 MiB，system RAM 为 14,418–16,356 MiB。
- 受管进程 stop → start → stop 审计通过；脱敏证据位于
  `docs/evidence/phase-5/evidence.json`，不含 prompt、reference name/content、
  image bytes、credentials 或 absolute paths。

正常 product binding 只有在 `support_state=verified` 后接受 v2；资格工具的
candidate-only binding 明确使用 `require_verified=False`，并仍校验 exact
variant、workflow digest 和 output node。真实浏览器验收尚未执行，等待用户按
`docs/TESTING.md` 的 Phase 5 procedure 确认后再进入 Phase 6 集成。
