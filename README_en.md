# h3chain

[中文文档](README.md)

**Genuinely coherent** unlimited-length video generation with MiniMax H3 —
not clips stitched together, but a true continuation: every clip grows out
of the previous one's motion-context latent, so characters, motion, camera
and lighting carry on seamlessly. Generate clip by clip, keep or redo,
then export one seamless long video.

```
 gen → watch → keep ─┬─ gen (continues from previous latent) → ... → export
                    └─ redo (fresh seed) → gen → ...
```

Locked clips come back from the Extender disk cache in seconds, so you only
ever pay for the clips you reject.

## Why "genuinely coherent"

Most long-video pipelines are **segment generation + first/last-frame
locking**: each clip is generated independently and force-aligned by its
boundary frames — motion "connects", but rhythm, physical momentum and
lighting continuity break at every seam.

h3chain takes the other road: the MiniMax H3 Extender caches the previous
clip's **motion-context latent** to disk, and the next clip samples directly
on top of it — not aligned, but **continued**. A run keeps running with real
momentum, a turn carries inertia, sunset light dims across clips.

- **Latent-level continuation**: context passes in latent space, not
  pixel-level frame locking
- **Physical & rhythm continuity**: motion momentum, camera movement and
  lighting transition naturally across clips
- **Unlimited length**: disk caching keeps the chain arbitrarily long
  without degradation
- **Simple web UI** (port 6008): per-clip prompt editor, reference upload,
  live SSE progress, inline player.
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
