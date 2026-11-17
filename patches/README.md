# Local llama.cpp patches

**English** · [Castellano](README.es.md)

The llama.cpp on this machine comes from the fork **github.com/PrivacyPolicy404/llama.cpp**, branch `ot-host-buffers`:
upstream `master` plus the 10 patches below and notes in the fork's README. In `~/llama.cpp`:

- `origin` = the fork (the branch is pushed there)
- `upstream` = ggml-org/llama.cpp, read-only (push is disabled)

To update without losing the patches:

```bash
~/llama-panel/scripts/update-llama.sh --check   # how many new commits there are
~/llama-panel/scripts/update-llama.sh           # rebase + build + verify + push to the fork + restart the router
```

If a patch no longer applies cleanly, the script changes nothing and says so.
The `.patch` files are copies of the commits (without the README notes), in case they need to be reapplied by hand
with `git am`. Hardware: AMD RX 580 8 GB (Polaris/GCN, no `shaderFloat16`), Vulkan only, i7-10700, 64 GB RAM.

## 1. `llama.cpp-ot-host-buffers.patch`

`-ot` / `--override-tensor` accepts pinned-memory buffers (`Vulkan_Host`, `CUDA_Host`, ...), so only part of the
tensors that live in RAM gets pinned. On amdgpu, pinned memory (GTT) is capped at half the RAM (31 GiB here) and
Flash-Next's experts take 31.6 GiB: pinning all of them crashes the driver.

Usage: `-lm none --lazy-mode on --no-host -ot "blk\.([0-9]|[1-3][0-9]|4[0-3])\.ffn_.*_exps=Vulkan_Host,ffn_.*_exps=CPU"`
(44 of 48 layers pinned). Long prompt 104 -> 125 tok/s. **Without this patch the Flash-Next settings don't load.**

## 2. `llama.cpp-vulkan-lightning-indexer-fp32.patch`

The RX 580 has no `shaderFloat16` and the Vulkan lightning indexer required it, so it ran on the CPU: two extra
GPU↔CPU round trips in each of Flash-Next's 12 sparse-attention layers. The patch adds a shader variant that reads
K and the mask in 16 bits but computes in 32. `test-backend-ops`: 210/210 OK.
Splits per token 122 -> 98; generation +4–5 % (temperature 0, no MTP). It takes 0.27 ms per token on the GPU.

## 3. `llama.cpp-quantize-copy-tensor-type.patch`

`llama-quantize INPUT OUTPUT COPY --tensor-type 'pattern=type'` requantizes only the named tensors and copies the
rest as is (before, `COPY` ignored `--tensor-type`). Used to build Flash-Next with the hyper-connections in Q8_0:

```bash
llama-quantize --keep-split --tensor-type 'hc_(attn|ffn)_(up|down)=q8_0' \
    ORIGINAL-00001-of-00002.gguf OUTPUT-hcq8.gguf COPY 8
```

## 4. `llama.cpp-vulkan-multi-add-amd.patch`

Vulkan's `multi_add` fusion (chains of same-size f32 adds in a single shader) was tied to
`shaderRoundingModeRTEFloat16`, which the RX 580 doesn't have. That check excludes the drivers where it fails
(Intel ANV, see PR #15252), but also Polaris. The shader is f32 only: the patch enables it on AMD drivers.
It covers the 9 adds that combine the 10 experts of each layer: 2980 -> 2591 operations per token.
`test-backend-ops -o ADD`: 116/116 OK. Flash-Next +2.5 %, Qwen3.6 +3 % (through the router).
To turn it off: `GGML_VK_DISABLE_MULTI_ADD=1`.

## 5. `llama.cpp-cpu-q2_0-avx2-prep.patch`

New AVX2 kernel for Flash-Next's Q2_0 experts on the CPU (`MUL_MAT_ID`, generation). The old one processed
3.8 GB/s per thread: compute was the limit, not RAM. The new one reorders the Q8_0 activations once per column to
match how the 2-bit weights are packed (unpacking 32 weights: one shift and one AND instead of 6 instructions),
computes `sum(y)` and the scales only once, and prefetches the weights 1 KiB ahead.
Microbenchmark: 5.1 -> 11.2 GB/s per thread; 8 threads from RAM 33 -> 40 GB/s (this machine's ceiling).
Flash-Next without MTP 13.5 -> 15.2 tok/s; **through the router 15.7 -> 17.6**. Prompt doesn't change (with large
batches the experts are computed on the GPU). `test-backend-ops -o MUL_MAT_ID -b Vulkan0`: all `q2_0` cases OK.

## 6. `llama.cpp-vulkan-sparse-attn-gcn.patch`

Flash-Next's attention picks ~2048 cells per step. Vulkan's sparse path (gathers only those cells) was enabled with
quantized K/V only once the cache was 16× the selection, a break-even measured on RDNA3/RDNA4. On the RX 580 the
dense path is slower and the sparse one already wins at 4×: the patch lowers the threshold only on `AMD_GCN` with
Q8_0 K/V. Effect: the sparse path is used from ~8K of context instead of ~32K. With MTP: 20K 17.1 -> 18.3 tok/s,
30K 16.4 -> 17.4. Adds Q8_0 cases to `test-backend-ops` (1 and 3 queries, selection 2051, cache 8K to 64K): 9/9 OK.

## 7. `llama.cpp-vulkan-first-submit-nodes.patch`

`GGML_VK_FIRST_SUBMIT_NODES=N`: the first batch of each graph is submitted to the GPU after N nodes (then every
`GGML_VK_MAX_NODES_PER_SUBMIT` as usual). With the experts on the CPU there are ~49 GPU splits per token and in
each one the GPU waited for 16 nodes to be recorded. Off by default; the panel sets it to 2 in the router unit.
Without MTP 14.70 -> 15.13 tok/s (+2.9 %), with MTP +0.8–1.4 %, identical output.

## 8. `llama.cpp-qwen4exp-mtp-d2t.patch`

The MTP draft spends more than half its time in the output projection (248320 rows), and almost every token it
proposes comes from a small part of the vocabulary. If the draft GGUF has a `d2t` tensor (I64, K token ids),
`output.weight` is loaded with K rows and the logits are scattered over the full vocabulary with the rest at -inf:
the same convention the engine already uses for eagle3 and dflash (the idea comes from FR-Spec, arXiv 2502.14856).
The model still verifies against the whole vocabulary, so the output distribution doesn't change.
The GGUF is built with `tools/mtp_vocab_order.py` (frequency order) and `tools/gguf_mtp_d2t.py` (trimming).
With the 65536 most frequent tokens: 18.8 -> 19.2 tok/s at 12K and 17.6 -> 18.0 at 30K, same drafts accepted,
VRAM 7.34 -> 7.10 GiB, draft 263 MB smaller.

## 9. `llama.cpp-vulkan-kq-matvec-fold-scales.patch`

The q3_K and q5_K matrix-vector shaders redid the scale products for every column and kept the weights, the scales
and (in q5_K) the per-group sums alive at the same time. With 4 columns that went over 128 vector registers
(147 and 143), and on GCN only one wave per SIMD fits instead of two: q3_K 10240×2560 took 226 µs with 3 columns and
369 with 4. Now the scales are folded into the weights once per row and each column is a dot product.
With 4 columns: 369 -> 265 µs (q3_K) and 290 -> 221 µs (q5_K); 1 to 3 columns up to 7 % faster.
`test-backend-ops` MUL_MAT 2592/2592 and MUL_MAT_ID 1160/1160.
Flash-Next with 2 MTP drafts: 19.50 -> 19.71 tok/s at 12K and 17.92 -> 17.98 at 30K. See log 5.28.

## 10. `llama.cpp-vulkan-iq4xs-matvec-halves.patch`

The same register threshold as patch 9, but in the everyday case: the iq4_xs shader kept the 32 B values of its
sub-block alive for each column (41 registers per column), and with 3 columns (the verification batch with 2 MTP
drafts) it reached 142 vector registers. With 2 or more columns it now processes the sub-block in two halves of 16
values (117 registers with 3 columns); a single column keeps the original scheme, which is slightly faster there.
10240×2560 with 3 columns: 283 -> 173 µs. `test-backend-ops` MUL_MAT 2592/2592 and MUL_MAT_ID 1160/1160.
Flash-Next with 2 MTP drafts: 19.62 -> 19.76 tok/s at 12K and 18.00 -> 18.43 at 30K. See log 5.29.

"Log" refers to the sections of [docs/analisis-rendimiento.md](../docs/analisis-rendimiento.md) (in Spanish).

## Besides the patches

The fork's branch also has commits that are not engine patches: the README notes, `docs/rx580.md`
(setup, options, what each patch changes and how to reproduce the numbers) and `scripts/mtp-d2t/` (English versions,
without local paths, of `tools/mtp_vocab_order.py` and `tools/gguf_mtp_d2t.py`, so the fork doesn't depend on this repo).

## If proposed upstream

llama.cpp's rules (`CONTRIBUTING.md`, `AGENTS.md`) ask to open an issue first, to disclose AI use, and for the PR
description and commit messages to be written by a person. The fork's commits have AI-written messages: for a PR
they need to be redone with your own messages, one per PR, without the fork's README notes.
