# llama-panel

**English** · [Castellano](README.es.md) · [![tests](https://github.com/PrivacyPolicy404/llama-panel/actions/workflows/tests.yml/badge.svg)](https://github.com/PrivacyPolicy404/llama-panel/actions/workflows/tests.yml)

A small home web panel to start and stop models on demand on a GPU with little VRAM (Vulkan):

- **Text:** `llama-server` (llama.cpp) in router mode, port 8080.
- **Images:** KoboldCpp with StableUI, port 5001.
- **Panel:** port 8090. Text and images are mutually exclusive so they can share the VRAM.

It only uses the Python 3 standard library: no dependencies, no build step. The interface is available in English
and Spanish (selector at the top right).

![Panel with Qwen3.8-Flash-Next loaded: VRAM, GPU, temperature and RAM at the top; text and image services; model list with their measured speeds](docs/img/panel.png)

It is built and measured on one specific machine (AMD RX 580 8 GB, Vulkan only, 64 GB RAM, Linux with systemd);
see [Compatibility](#compatibility) for others.
The performance log ([docs/analisis-rendimiento.md](docs/analisis-rendimiento.md)) and the hardware notes
([docs/notas.md](docs/notas.md)) explain every setting with numbers and may help on other low-VRAM GPUs.
They are written in Spanish; each one starts with an English summary of the main findings.

> **Security:** the panel (8090), llama-server (8080) and KoboldCpp (5001) listen on all interfaces and **have no
> authentication**: anyone who can reach those ports can load, unload and configure models. It is meant for a
> trusted local network only. Do not expose them to the internet; for remote access use a VPN or an SSH tunnel,
> or restrict the ports with a firewall.

## What it does

- Monitors VRAM, GPU usage, temperature/power/fan and RAM, with a history of the last few minutes.
- Starts, restarts and stops text and images; starting one stops the other to free VRAM.
- Loads and unloads a single text model (llama-server router API) without restarting anything.
- Per-model settings with plain controls for the options that matter on a low-VRAM GPU (context, GPU layers,
  MoE experts in RAM, parallel requests, KV cache, flash attention, prompt batch, threads, MTP, reasoning), each
  with a short explanation and separate values for AI mode if it is installed. Anything else goes in the extra
  options, which win over the controls (the panel shows which ones they override); "Save and apply" reloads only what is needed.
- Saved initial prompt: with an agent-style client (Cline, etc.), "Save prompt" writes the already-processed fixed
  prompt to disk and the panel restores it every time the model loads (about a minute less waiting per session).
- Picks the image model and its KoboldCpp flags, with the progress of the current generation.
- Performance: tokens/s per response (prompt and generation, MTP acceptance) and duration of each image.
- Live logs of each service, with a filter.
- A **Setup** tab to choose model folders and programs from the browser.

## Layout

```
panel.py              entry point: runs the server or --install
llama_panel/
  config.py           paths, ports, settings.json
  paths.py            Setup tab: paths in paths.env, validation, suggestions
  i18n.py             server messages in Spanish or English
  text_models.py      .gguf scan, router presets.ini, validation
  prompt_cache.py     saved initial prompt: slot state to disk and restore on load
  img_models.py       image scan, companions (VAE/encoders), kobold.env
  services.py         systemctl and HTTP clients for the router and KoboldCpp
  hardware.py         GPU/RAM sensors (amdgpu, nvidia-smi) and background sampling
  stats.py            journalctl parsing: tokens/s, images, logs
  actions.py          what the buttons do (load, save, apply, start)
  api.py              /api/* routes
  server.py           HTTP server
  install.py          systemd units (and keeping them in sync at startup)
  kiosk.py            text dashboard for "AI mode" (tty1)
static/
  index.html, app.css
  js/                 native ES modules, no build (main.js starts everything; i18n.js has the translations)
tests/                stdlib unittest
scripts/
  update-llama.sh     updates llama.cpp keeping the patches (--check only reports)
  install-gpu-mclk.sh optional, with sudo: VRAM clock at max while a model runs (AMD Polaris)
  install-modo-ia.sh  optional, with sudo: "AI mode" (stops the desktop, dashboard on tty1)
patches/              local llama.cpp patches and how to reapply them
tools/bench/          benchmarks, CPU/GPU usage, GGUF sizes and model smoke test
tools/                GGUF utilities (hc in Q8_0, MTP draft with trimmed output)
docs/                 hardware notes and performance log (Spanish, with English summaries)
.claude/skills/       llama-cpp-update skill (review and update llama.cpp)
```

## Requirements

- Linux with systemd (user services) and Python 3.
- `llama-server` from **upstream** [llama.cpp](https://github.com/ggml-org/llama.cpp) (a recent version, with router
  mode); by default it is looked up at `~/llama.cpp/build/bin/llama-server`. The fork below is only needed for some
  settings of large MoE models.
- Optional, for images: [KoboldCpp](https://github.com/LostRuins/koboldcpp) (default `~/koboldcpp/koboldcpp`).
- `.gguf` models in `~/models` and image models in `~/imgmodels`, or wherever the configured paths say.

### Compatibility

| | Status |
|---|---|
| AMD with `amdgpu` + Vulkan (RX 580) | **Tested**: the panel's day-to-day use runs here |
| Other AMD with `amdgpu` (RDNA, etc.) | Should work, untested. Same sensors (sysfs) |
| NVIDIA (CUDA or Vulkan) | Should work, untested. Sensors come from `nvidia-smi` (no GTT memory). For images, add `--usecuda` to the model options |
| Intel or others | Should work, untested, but **without GPU data** (VRAM, usage, temperature) |
| Upstream llama.cpp | Should work, untested on this machine: the panel only uses upstream features (router, `/slots`, MTP) |
| AI mode with SDDM (KDE) | **Tested**. With GDM, LightDM or another display manager it should work, untested |

`scripts/install-gpu-mclk.sh` (VRAM clock and overclock) is for AMD Polaris (RX 470–590) only.
The GPU name is detected automatically; if it comes out wrong, set `LLAMA_GPU_NAME` in `paths.env`.

## Installation

```bash
git clone https://github.com/PrivacyPolicy404/llama-panel.git ~/llama-panel
cd ~/llama-panel && python3 panel.py --install
```

This creates the systemd user services (`llama-panel`, `llama-router`, `kobold-img`) and starts the panel at
`http://<machine>:8090`. If it can't find `llama-server` or any models, it opens the **Setup** tab: there you choose
the model folders (with suggestions as you type and how many models each one has) and the programs (it shows the
llama.cpp version it found). On save, the panel restarts itself and rescans the models.

User configuration lives in `~/.config/llama-panel/` (outside the repo). Paths are stored in `paths.env`, which can
also be edited by hand; an environment variable with the same name takes precedence (and locks that field in the
panel):

```ini
LLAMA_MODELS_DIR=/mnt/data/models
LLAMA_EXTRA_MODELS_DIRS=~/models-nvme
LLAMA_IMG_DIR=/mnt/data/imgmodels
KOBOLD_BIN=~/koboldcpp/koboldcpp
LLAMA_SERVER_BIN=~/llama.cpp/build/bin/llama-server
```

The `scripts/install-*.sh` scripts are optional, run once with `sudo` and have `--uninstall`.
Each one installs a helper in `/usr/local/sbin` and a rule in `/etc/sudoers.d` that lets your user run **only** that
helper without a password. AI mode **closes the graphical session** when turned on; read each script's header first.

> [!WARNING]
> **AI mode is meant for a dedicated machine that you use remotely** (a home server with its own monitor, used
> through the browser or SSH from another computer). It stops the display manager: **the graphical session closes
> and anything open and unsaved in it is lost**. If you turn it on from the same PC you are sitting at, you are
> left without a desktop until you leave AI mode, either from the panel on another device (phone, laptop) or on
> that same PC: press S twice on the dashboard, or Ctrl+Alt+F2, log in and run `sudo llama-modo-ia off`.
> If you use the panel on your everyday desktop PC, don't install AI mode: everything else works without it.

In AI mode the desktop is stopped to free the VRAM it was using, and tty1 shows this text dashboard
(`llama_panel/kiosk.py`, in Spanish; to see it over SSH without turning anything on: `python3 -m llama_panel.kiosk --once`):

![AI mode dashboard on tty1: GPU, per-core CPU, RAM, loaded model, current response and processes](docs/img/modo-ia.png)

## llama.cpp: why a fork

The panel works with upstream llama.cpp. On this machine it uses the fork
[PrivacyPolicy404/llama.cpp](https://github.com/PrivacyPolicy404/llama.cpp) (branch `ot-host-buffers`): upstream
plus local patches, mostly performance work for Polaris (RX 580) on Vulkan.
The main one exists because of large MoE models that don't fit in VRAM, such as Qwen3.8-Flash-Next (125B):

- Their experts live in RAM. If they are in *pinned* memory, the GPU copies them faster and prompt processing is ~20 % faster.
- On amdgpu, pinned memory is capped (GTT) at half the RAM: 31 GiB on this machine. Flash-Next's experts take 31.6 GiB:
  pinning all of them crashes the driver ("device lost").
- Upstream llama.cpp only allows pinning everything or nothing. The patch lets you pick which layers to pin with
  `-ot ...=Vulkan_Host`: 44 of 48 layers fit under the cap.

Without the patch that model doesn't load with the panel's settings; the other models don't need it.

**Want to run Qwen3.8-Flash-Next at ~20 tok/s on an RX 580?** [docs/flash-next-rx580.md](docs/flash-next-rx580.md)
has the exact panel configuration, and the fork's guide
([docs/rx580.md](https://github.com/PrivacyPolicy404/llama.cpp/blob/ot-host-buffers/docs/rx580.md)) how to build it
and prepare the model files.
Details, numbers and how to reapply it: [patches/README.md](patches/README.md).

To update llama.cpp without losing the patches: `scripts/update-llama.sh` (`--check` only reports how many new commits
there are). The version stays pinned until the script is run.

## Development

The service runs the code straight from this folder and `llama-panel-reload.path` restarts the panel every time
`panel.py` or anything in `llama_panel/` is saved. Files in `static/` are read on every request, so reloading the
browser is enough.

If the systemd units change (`llama_panel/install.py`), the panel rewrites them at startup and runs `daemon-reload`
(without restarting the services). `--install` is only needed the first time.

```bash
python3 -m unittest                   # tests (they also check every UI text has its English translation)
journalctl --user -u llama-panel -f   # panel errors
```

To add a UI text: write it in Spanish inside `t('…')` and add its English version to `static/js/i18n.js`; for server
messages, `_("…")` and `llama_panel/i18n.py`.

To validate the 64K window through the router, with 62,000 input tokens and 2,048 output tokens,
`tools/bench/ctx_stability.py` records VRAM/GTT memory per process and checks three loads with generation.
It needs the servers and AI mode off. It **closes the graphical session** during the test and restores the desktop
and memory limits at the end:

```bash
python3 tools/bench/ctx_stability.py --ai-mode --output ~/llama-panel-results/64k-run-1
```

The output folder must be new. It keeps responses, input tokens, timestamped samples by phase, presets, versions
and router/kernel logs. It can take more than 15 minutes. Forced generation tests stability, not semantic quality;
speeds depend on the text and on how full the context is.

To check information retrieval and conversation with ~58K tokens of the project itself:

```bash
python3 tools/bench/ctx_quality.py --ai-mode --output ~/llama-panel-results/64k-quality-1
```

It stores the expected answers before inference, checks new markers spread across the context, facts from the code,
several turns, prompt reuse and a conversation close to the limit. It also records the server's response to an
input that exceeds the window. It needs the servers off; it keeps the initial AI mode state and the settings. It is
a one-off evaluation, without an F16 control or comparison between models.

To compare one and two slots, with two jobs in series/concurrently and, optionally, cached follow-ups:

```bash
python3 tools/bench/parallel_test.py --output ~/llama-panel-results/parallel-1
python3 tools/bench/parallel_test.py --followup --output ~/llama-panel-results/parallel-conversation-1
```

It needs AI mode on and Flash-Next loaded and idle. It unloads the panel's model (losing the cache), uses an
isolated router and restores the initial load. It doesn't change settings. With 64K total, two slots get 32K each.
It measures 256 forced tokens per job; it doesn't evaluate the quality of a subagent or the timing of its tools.

Text models are looked up in `LLAMA_MODELS_DIR` and in the folders of `LLAMA_EXTRA_MODELS_DIRS` (separated by `:`). Models split into shards (`-00001-of-0000N.gguf`) count as one.

Optional environment variables: `LLAMA_MODELS_DIR`, `LLAMA_EXTRA_MODELS_DIRS`, `LLAMA_IMG_DIR`, `LLAMA_SERVER_BIN`,
`KOBOLD_BIN`, `LLAMA_GPU_NAME`, `LLAMA_PANEL_PORT`, `LLAMA_PANEL_DIR`.

## License

[MIT](LICENSE). llama.cpp and KoboldCpp have their own licenses.
