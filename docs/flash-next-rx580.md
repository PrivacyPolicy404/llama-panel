# Qwen3.8-Flash-Next (125B MoE) at ~20 tok/s on an RX 580, with llama-panel

This is the exact panel configuration that runs
[Qwen3.8-Flash-Next](https://huggingface.co/SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF) — 125B parameters,
6B active, 66 GB on disk — on a Radeon RX 580 8 GB with 64 GB of RAM:

| | |
|---|---|
| Generation | ~20 tok/s with a short prompt, 19.8 at 12K of context, 18.4 at 30K, ~18 at 61K |
| Prompt processing | ~134 tok/s (3K tokens), 92 tok/s (61K) |
| Context | 8K with the desktop running, 64K in AI mode |
| Without any tuning | 12.8 tok/s, 62 tok/s prompt, 8K |

How to build the engine, which files to download and why each option is there is explained in the fork's guide,
[**Running a 125B MoE on an RX 580**](https://github.com/PrivacyPolicy404/llama.cpp/blob/ot-host-buffers/docs/rx580.md).
This page only covers how to enter that configuration in the panel. The full log of experiments (in Spanish, with
an English summary) is in [analisis-rendimiento.md](analisis-rendimiento.md).

## What you need

- A Polaris card with 8 GB (RX 470/480/570/580/590) or similar, **64 GB of RAM** and **~100 GB free on an NVMe**.
  The second shard holds an n-gram table that is read from disk on every token: on a hard drive it crawls.
- The fork [PrivacyPolicy404/llama.cpp](https://github.com/PrivacyPolicy404/llama.cpp), branch `ot-host-buffers`,
  built with Vulkan ([Build](https://github.com/PrivacyPolicy404/llama.cpp/blob/ot-host-buffers/docs/rx580.md#build)).
  Upstream llama.cpp does **not** load this configuration: it can't pin only part of the experts (patch 1).
- The model files and the two derived ones (hyper-connections in Q8_0, and the MTP draft with a trimmed vocabulary),
  following [The model files](https://github.com/PrivacyPolicy404/llama.cpp/blob/ot-host-buffers/docs/rx580.md#the-model-files).
  You should end up with, in one folder on the NVMe:
  ```
  Qwen3.8-Flash-Next-GSQ-RCO-abliterated-Q2_0-hcq8-00001-of-00002.gguf
  Qwen3.8-Flash-Next-GSQ-RCO-abliterated-Q2_0-hcq8-00002-of-00002.gguf
  mtp-Qwen3.8-Flash-Next-Q4_0-d2t65k.gguf
  ```
  (The MTP draft starts with `mtp-`, so the panel doesn't list it as a model of its own.)

## Panel setup

1. **Setup tab:** llama-server = the fork's `build/bin/llama-server`; add the NVMe folder to the text model folders.
2. **Optional but worth it:** `sudo scripts/install-gpu-mclk.sh` pins the VRAM clock while a model runs (+2–3 %).
   The panel already sets `GGML_VK_MAX_NODES_PER_SUBMIT=16` and `GGML_VK_FIRST_SUBMIT_NODES=2` for the text server.
3. **Optional:** AI mode (`scripts/install-modo-ia.sh`) for 64K of context. Only on a dedicated machine: it closes
   the graphical session (see the README).

## Model settings

Open **Settings** on the `…-hcq8` model and set:

| Control | Normal | AI mode |
|---|---|---|
| Context | (empty: global, 8192) | 65536 |
| GPU layers | 99 | same as normal |
| MoE experts in RAM | (empty: the extra options below place them) | same as normal |
| Parallel requests | 1 | same as normal |
| KV cache | default (f16) | q8_0 |
| Prompt batch | 2048 | same as normal |
| MTP | on | same as normal |
| Drafts per step | 2 | same as normal |
| Reasoning | to taste | same as normal |

**Extra llama-server options** (change the path of the draft):

```ini
# Experts of layers 0-43 in pinned memory (29.4 GiB of GTT, the cap is half the RAM), 44-47 in normal RAM;
# n-gram table read from the NVMe. Needs the fork (patch 1).
load-mode = none
lazy-mode = on
no-host = 1
override-tensor = blk\.([0-9]|[1-3][0-9]|4[0-3])\.ffn_.*_exps=Vulkan_Host,ffn_.*_exps=CPU
# MTP draft with its output trimmed to the 65536 most frequent tokens (patch 8)
spec-draft-model = /path/to/mtp-Qwen3.8-Flash-Next-Q4_0-d2t65k.gguf
# Drafts sampled and verified by rejection (same output distribution as without MTP): +2 %
spec-draft-sampling = probabilistic
spec-draft-p-min = 0
# Draft in batches of 512: its buffers drop from 867 to ~220 MiB at the same speed
spec-draft-n-ubatch = 512
batch-size = 2048
```

**Extra options in AI mode:**

```ini
# The draft's KV cache in Q8_0 too (64K takes ~7.4 GiB of VRAM in total)
cache-type-k-draft = q8_0
cache-type-v-draft = q8_0
```

Save and apply, then load the model: the first load reads 66 GB and takes a few minutes. The panel's
**Performance** tab shows the speed of each response and the MTP acceptance (~70–80 %).

## If something is off

- **`error while handling argument "--override-tensor": unknown buffer type`** in the router log: the panel is
  using upstream llama.cpp. Point it to the fork's `llama-server` in the Setup tab.
- **~2 tok/s and the GPU idle:** Vulkan fell back to `llvmpipe` (no access to `/dev/dri`, typical without a desktop
  session): add your user to the `render` and `video` groups and reboot.
- **`device lost` while loading:** more pinned memory than the GTT cap. With less than 64 GB of RAM, pin fewer
  layers: lower `4[0-3]` in `override-tensor` (each layer is ~0.66 GiB).
- **Half the speed:** `parallel` isn't 1 (the router defaults to 4 slots).
- **Slow long prompts in AI mode at 64K:** check VRAM in the panel; at 7.9+ GiB the driver spills to GTT. 80K does
  not fit on 8 GB.
- Anything else: the [Things that were measured and did not help](https://github.com/PrivacyPolicy404/llama.cpp/blob/ot-host-buffers/docs/rx580.md#things-that-were-measured-and-did-not-help)
  section of the fork's guide, then an issue with your GPU, driver and the router log.
