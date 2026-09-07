# h3chain

[中文文档](README.md)

The [MiniMax H3 Extender](https://github.com/tritant/ComfyUI_MiniMax_H3_Extender)
is the engine of this project — it achieves genuinely coherent chained
generation via on-disk motion-context latents: every clip continues from
the previous one's latent, so characters, motion and lighting carry on
seamlessly, and the chain can be arbitrarily long. **All credit for that
capability goes to the Extender's authors.**

What h3chain adds is simple: it wraps that powerful chain into an
**out-of-the-box web UI** — no node graphs, no hand-written JSON. Create a
project, upload references, write prompts per clip, then generate, keep or
redo with one click and export the full video.

```
 gen → watch → keep ─┬─ gen (Extender continues from previous latent) → ... → export
                    └─ redo (fresh seed) → gen → ...
```

## What h3chain provides

On top of the Extender, this web layer fills in the "easy to use" part:
- **Zero-barrier web UI** (port 6008): per-clip prompt editor, reference
  upload with thumbnail preview, live SSE progress, inline player —
  no ComfyUI involved
- **Project management**: isolated projects (story/refs/outputs), story.json
  validation and concurrent-write protection
- **Per-clip polishing loop**: generate → watch → keep/redo; redo rotates
  the seed automatically, never silently hitting the Extender disk cache
- **Resumable export**: locked clips come back from cache in seconds
  (you only pay for the clips you reject); export fills the gaps and
  merges the chain.
- **24 GB friendly**: quantized weight set (int8 UNet + nvfp4 text encoder),
  runs on a single RTX 4090.
- **Cache-aware redo**: redo automatically rotates the seed — submitting the
  same prompt+seed silently hits the Extender disk cache and returns the old
  clip; h3chain never falls into that trap.
- **Resumable export**: validated clips are cached; export only generates
  what's missing, then FinalDecode merges the full chain.

## Quick start (on an AutoDL / any ComfyUI box)

```bash
git clone https://github.com/xt-mahh/h3chain.git
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

The core capability of this project comes from the following work;
h3chain only makes it easier to use:

- **[ComfyUI_MiniMax_H3_Extender](https://github.com/tritant/ComfyUI_MiniMax_H3_Extender)**
  — the real engine of coherent chained generation (on-disk motion-context
  latents, clip_by_clip mode, FinalDecode merging). Thanks to tritant and
  contributors.
- [MiniMax H3](https://github.com/MiniMax-AI/MiniMax-H3) — model weights,
  their license applies (see THIRD_PARTY_NOTICES.md)
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) + KJNodes + SageAttention +
  lightx2v (Turbo LoRA) + Comfy-Org (quantized weight repack)

h3chain itself is MIT licensed — see LICENSE.
