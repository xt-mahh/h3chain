# h3chain

[中文文档](README_zh.md) | [English](README.md)

Unlimited-length video generation with MiniMax H3 — a thin web app on top of
ComfyUI's MiniMax H3 Extender chain. Generate clip by clip, keep what you
like, redo what you don't, then export the whole chain as one video.

```
 gen → watch → keep ─┬─ gen → ... → export (merge all)
                    └─ redo (fresh seed) → gen → ...
```

Locked clips come back from the Extender disk cache in seconds, so you only
ever pay for the clips you reject.

## What it is

- **Unlimited length**: H3 chains clips via motion-context latents on disk.
  h3chain drives that chain clip-by-clip with a simple loop: generate →
  review → keep/redo.
- **Simple web UI** (port 6008): story.json editor, clip list, live SSE
  progress, inline player. No ComfyUI graph editing needed.
- **24 GB friendly**: quantized weight set (int8 UNet + nvfp4 text encoder),
  runs on a single RTX 4090.
- **Cache-aware redo**: redo automatically rotates the seed — submitting the
  same prompt+seed silently hits the Extender disk cache and returns the old
  clip; h3chain never falls into that trap.
- **Resumable export**: validated clips are cached; export only generates
  what's missing, then FinalDecode merges the full chain.

## Quick start (on an AutoDL / any ComfyUI box)

```bash
git clone https://github.com/<you>/h3chain.git
cd h3chain

bash install.sh            # custom nodes + sageattention + quantized weights (~42 GB)
                            # or: bash install.sh --no-models   to skip downloads

bash boot.sh                # starts ComfyUI (6006) + h3chain web UI (6008)
```

Open `http://<host>:6008/`, create a project, drop reference images into
`refs/`, edit `story.json` (a six-section Ref2VA template is pre-filled),
then: **generate → watch → keep / redo → export**.

See `demo/` for a complete 3-clip example story.

## Configuration

| Env | Default | Meaning |
|-----|---------|---------|
| H3CHAIN_WORKSPACE | ./src/workspace | project storage |
| H3CHAIN_COMFY_HOST / PORT | 127.0.0.1 / 6006 | ComfyUI address |
| H3CHAIN_TIMEOUT | 1800 | per-clip generation timeout (s) |

## API

See [docs/current/api.md](docs/current/api.md) — 12 endpoints, JSON envelope
`{ok, error, error_detail, data}`, SSE progress stream included.

## Development

```bash
python3 -m pytest tests/    # 30 tests, mock ComfyUI, no GPU needed
```

## Credits & licenses

- [MiniMax H3](https://github.com/MiniMax-AI/MiniMax-H3) — model weights,
  their license applies (see THIRD_PARTY_NOTICES.md)
- [ComfyUI_MiniMax_H3_Extender](https://github.com/tritant/ComfyUI_MiniMax_H3_Extender)
  — the Extender node doing the actual chained generation
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) + KJNodes + SageAttention

MIT License — see LICENSE.
