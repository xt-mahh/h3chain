# Third-party notices

h3chain itself is MIT. It builds on the following projects; each keeps its
own license — model weights are NOT redistributed in this repository.

## Model weights (downloaded at install time, not included here)

- **MiniMax H3** (Comfy-Org repack: int8 UNet, nvfp4 text encoder, fp16/fp32 VAEs,
  lightx2v turbo LoRA) — subject to the MiniMax H3 model license:
  https://huggingface.co/MiniMaxAI/MiniMax-H3 (see LICENSE there).
  Quantized repack served from https://huggingface.co/Comfy-Org/MiniMax-H3.

## Software dependencies

- **ComfyUI** — GPL-3.0 — https://github.com/comfyanonymous/ComfyUI
  (h3chain talks to a running ComfyUI over its HTTP API; it does not bundle
  ComfyUI code)
- **ComfyUI_MiniMax_H3_Extender** — see repo — https://github.com/tritant/ComfyUI_MiniMax_H3_Extender
  (installed into ComfyUI/custom_nodes by install.sh; the MiniMaxH3Extender /
  FinalDecode node chain is built and submitted via the ComfyUI API)
- **ComfyUI-KJNodes** — see repo — https://github.com/kijai/ComfyUI-KJNodes
  (PathchSageAttentionKJ node)
- **SageAttention** — Apache-2.0 — https://github.com/thu-ml/SageAttention
- **FastAPI / Starlette / Pydantic / uvicorn** — MIT
- **PyYAML, httpx** — MIT

If you redistribute the quantized weights (e.g. inside a container image),
check the MiniMax H3 model license terms for redistribution requirements
first.
