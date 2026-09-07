# h3chain

[English](README_en.md)

基于 MiniMax H3 的**真正连贯的**无限长视频生成——不是把一堆片段拼起来，
而是每一段都从前一段的运动上下文 latent 中**延续**出来：人物、动作、镜头、
光影自然延续，看不到接缝。逐 clip 生成，满意就锁定，不满意就重做，
最后导出一条浑然一体的长片。

```
 生成 → 看片 → 锁定 ─┬─ 生成（从上一段 latent 延续）→ ... → 导出（全链一体）
                    └─ 重做（自动换 seed）→ 生成 → ...
```

已锁定的 clip 下次直接命中 Extender 磁盘缓存秒回——你只为不满意的片段付费。

## 为什么是"真连贯"

多数长视频方案是**分段生成 + 首尾帧拼接**：每段独立生成，靠最后一帧
强制对齐下一帧——动作是"接上了"，但运动节奏、物理惯性、光影连续性全断。

h3chain 走的是另一条路：MiniMax H3 Extender 把上一段的**运动上下文 latent
缓存到磁盘**，下一段直接在这个 latent 上继续采样——不是"对齐"，是**延续**。
同一段奔跑会真的跑下去，转身有转身的惯性，日落的光线会一段段暗下去。

- **latent 级延续**：上下文在隐空间传递，而非像素级首尾帧锁定
- **物理与节奏连续**：动作惯性、镜头运动、光影变化跨段自然过渡
- **无限时长**：磁盘缓存让链条可以任意长，不随段数增加而退化
- **简单 Web 界面**（6008 端口）：分段 prompt 编辑、参考图上传、SSE 实时进度、内嵌播放器
- **24G 显存友好**：量化权重组合（int8 UNet + nvfp4 文本编码器），单张 RTX 4090 可跑
- **缓存感知重做**：redo 自动换 seed，永不静默命中旧缓存
- **可续跑导出**：已验证 clip 走缓存，export 只补缺的，FinalDecode 合并全链

## 快速开始（AutoDL / 任何 ComfyUI 机器）

```bash
git clone https://github.com/<you>/h3chain.git
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

- [MiniMax H3](https://github.com/MiniMax-AI/MiniMax-H3)——模型权重，
  遵循其许可（见 THIRD_PARTY_NOTICES.md）
- [ComfyUI_MiniMax_H3_Extender](https://github.com/tritant/ComfyUI_MiniMax_H3_Extender)
  ——真正做链式生成的 Extender 节点
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) + KJNodes + SageAttention

MIT License——见 LICENSE。
