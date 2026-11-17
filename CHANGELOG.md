# Changelog

## 0.1.0 — 2026-11-16

First public release. / Primera versión pública.

- Web panel (port 8090) to start and stop `llama-server` in router mode (text) and KoboldCpp + StableUI (images),
  which share the VRAM and are mutually exclusive.
- Load and unload text models without restarts; per-model settings with plain controls for the main
  llama-server options (context, GPU layers, MoE experts in RAM, parallel, KV cache, flash attention, batch,
  threads, MTP, reasoning), separate values for AI mode, and raw extra options for everything else.
- New installs no longer set `n-gpu-layers = 99` globally, so llama.cpp's automatic fit stays on.
- Saved initial prompt for agent-style clients, restored automatically when the model loads.
- Hardware monitor (VRAM, GPU, temperature, power, RAM) for amdgpu and, untested, NVIDIA via `nvidia-smi`.
- Performance tab (tokens/s per response, MTP acceptance, image timings) and live logs.
- Setup tab to choose model folders and programs from the browser, with validation and suggestions.
- English and Spanish interface.
- Optional "AI mode" for dedicated servers: stops the desktop and shows a text dashboard on tty1.
- Tested on AMD RX 580 8 GB with Vulkan. Works with upstream llama.cpp; some large-MoE settings need the fork
  [PrivacyPolicy404/llama.cpp](https://github.com/PrivacyPolicy404/llama.cpp) (`ot-host-buffers`).
