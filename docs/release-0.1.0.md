First public release of **llama-panel**: a small web panel to run llama.cpp and KoboldCpp models on demand on a
GPU with little VRAM. Standard-library Python only, no build step.

**What's in it**
- Start/stop text (`llama-server` router mode) and images (KoboldCpp + StableUI); they share the VRAM.
- Load/unload models without restarts and a saved initial prompt for agent clients.
- Per-model settings with plain controls for the options that matter on a low-VRAM GPU (context, GPU layers,
  MoE experts in RAM, parallel requests, KV cache, flash attention, batch, threads, MTP, reasoning), each one
  explained, with separate values for AI mode; raw llama-server options for everything else.
- Hardware monitor, per-response performance stats and live logs.
- Setup tab to pick model folders and programs from the browser.
- English and Spanish interface.
- Optional AI mode for **dedicated servers** (it closes the graphical session of the machine it runs on).

**Highlight: Qwen3.8-Flash-Next (125B MoE) at ~20 tok/s on an RX 580 8 GB.** The exact configuration is in
[docs/flash-next-rx580.md](https://github.com/PrivacyPolicy404/llama-panel/blob/main/docs/flash-next-rx580.md); it
needs the fork [PrivacyPolicy404/llama.cpp](https://github.com/PrivacyPolicy404/llama.cpp) (`ot-host-buffers`), whose
[guide](https://github.com/PrivacyPolicy404/llama.cpp/blob/ot-host-buffers/docs/rx580.md) explains the build and
every option.

**Tested on** AMD RX 580 8 GB, Vulkan (RADV), Kubuntu with SDDM. Upstream llama.cpp is tested too
(about 3 % slower than the fork; only the Flash-Next settings need the fork). Other AMD GPUs, NVIDIA, Intel and
other display managers should work but are untested: reports are welcome.

**Install**
```bash
git clone https://github.com/PrivacyPolicy404/llama-panel.git ~/llama-panel
cd ~/llama-panel && python3 panel.py --install
```
Then open `http://<machine>:8090`. Read the security note in the README: the panel has no authentication and is
meant for a trusted local network.

---

Primera versión pública. Panel web para levantar modelos de llama.cpp y KoboldCpp on demand en una GPU con poca
VRAM. Incluye la configuración para correr Qwen3.8-Flash-Next a ~20 tok/s en una RX 580. Probado en RX 580 con Vulkan; el resto debería funcionar sin probar. Detalles en
[README.es.md](https://github.com/PrivacyPolicy404/llama-panel/blob/main/README.es.md).
