# h3chain

[English](README_en.md)

[MiniMax H3 Extender](https://github.com/tritant/ComfyUI_MiniMax_H3_Extender)
是本项目的核心——它通过磁盘上的运动上下文 latent 实现了真正连贯的链式生成：
每一段从前一段的 latent 中延续出来，人物、动作、光影自然延续，看不到接缝，
且链条可以任意长。**这项能力完全来自 Extender 的作者们。**

h3chain 做的事情很简单：把这条强大的链封装成一个**开箱即用的 Web 界面**——
不用编辑节点图、不用手写 JSON，建工程、传参考图、分段写 prompt，
点按钮就能逐段生成、锁定、重做，最后导出全片。

```
 生成 → 看片 → 锁定 ─┬─ 生成（Extender 从上一段 latent 延续）→ ... → 导出
                    └─ 重做（自动换 seed）→ 生成 → ...
```

## h3chain 提供了什么

在 Extender 之上，这个 Web 封装层补齐了"好用"的部分：

- **零门槛 Web 界面**（6008 端口）：分段 prompt 编辑、参考图上传缩略图预览、
  SSE 实时进度、内嵌播放器——全程不碰 ComfyUI
- **工程化管理**：多工程隔离（story/refs/产物），story.json 校验与并发写保护
- **逐段打磨工作流**：生成 → 看片 → keep/redo；redo 自动换 seed，
  永不静默命中 Extender 磁盘缓存返回旧片段
- **可续跑导出**：已锁定 clip 走磁盘缓存秒回（只为不满意的片段付费），
  export 自动补齐缺失段并合并全链
- **24G 显存友好**：默认量化权重组合（int8 UNet + nvfp4 文本编码器）走
  AutoDL 公共模型库软链，单张 RTX 4090 可跑，镜像零权重

## 快速开始（AutoDL / 任何 ComfyUI 机器）

```bash
git clone https://github.com/xt-mahh/h3chain.git
cd h3chain

bash install.sh            # 自定义节点 + sageattention + 量化权重（约 42 GB）
                            # 或：bash install.sh --no-models   跳过下载

bash boot.sh                # 启动 ComfyUI（6006）+ h3chain Web 界面（6008）
```

浏览器打开 `http://<host>:6008/`，新建工程，参考图放进 `refs/`，
编辑 `story.json`（预填了六段式 Ref2VA 模板），然后：
**生成 → 看片 → 锁定 / 重做 → 导出**。

完整 3-clip 示例见 `demo/`。

## 配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| H3CHAIN_WORKSPACE | ./src/workspace | 工程存储目录 |
| H3CHAIN_COMFY_HOST / PORT | 127.0.0.1 / 6006 | ComfyUI 地址 |
| H3CHAIN_TIMEOUT | 1800 | 单 clip 生成超时（秒） |

## API

见 [docs/current/api.md](docs/current/api.md)——12 个端点，JSON 信封
`{ok, error, error_detail, data}`，含 SSE 进度流。

## 开发

```bash
python3 -m pytest tests/    # 30 个测试，mock ComfyUI，无需 GPU
```

## 致谢与许可

本项目的核心能力来自以下工作，h3chain 只是把它们变得更易用：

- **[ComfyUI_MiniMax_H3_Extender](https://github.com/tritant/ComfyUI_MiniMax_H3_Extender)**
  ——真正做连贯链式生成的功臣（运动上下文 latent 磁盘缓存、clip_by_clip 模式、
  FinalDecode 合并都是它的实现），感谢 tritant 及其贡献者
- [MiniMax H3](https://github.com/MiniMax-AI/MiniMax-H3)——模型权重，
  遵循其许可（见 THIRD_PARTY_NOTICES.md）
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) + KJNodes + SageAttention +
  lightx2v（Turbo LoRA）+ Comfy-Org（量化权重复打包）

h3chain 本身以 MIT License 开源——见 LICENSE。
