First public release of **llama-panel**: a small web panel to run llama.cpp and KoboldCpp models on demand on a
GPU with little VRAM. Standard-library Python only, no build step.

**What's in it**
- Start/stop text (`llama-server` router mode) and images (KoboldCpp + StableUI); they share the VRAM.
- Load/unload models without restarts, per-model settings, saved initial prompt for agent clients.
- Hardware monitor, per-response performance stats and live logs.
- Setup tab to pick model folders and programs from the browser.
- English and Spanish interface.
- Optional AI mode for **dedicated servers** (it closes the graphical session of the machine it runs on).

**Tested on** AMD RX 580 8 GB, Vulkan (RADV), Kubuntu with SDDM. Other AMD GPUs, NVIDIA, Intel, upstream
llama.cpp and other display managers should work but are untested: reports are welcome.

**Install**
```bash
git clone https://github.com/PrivacyPolicy404/llama-panel.git ~/llama-panel
cd ~/llama-panel && python3 panel.py --install
```
Then open `http://<machine>:8090`. Read the security note in the README: the panel has no authentication and is
meant for a trusted local network.

---

Primera versión pública. Panel web para levantar modelos de llama.cpp y KoboldCpp on demand en una GPU con poca
VRAM. Probado en RX 580 con Vulkan; el resto debería funcionar sin probar. Detalles en
[README.es.md](https://github.com/PrivacyPolicy404/llama-panel/blob/main/README.es.md).
