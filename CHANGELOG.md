# Changelog

## Unreleased

- Read-only buddy tools for one short web search per turn and ephemeral client context (language and timezone,
  plus approximate location only when explicitly enabled on that device). Search results are limited to fixed
  providers and treated as untrusted content; location coordinates are rounded before being sent and never saved.
- Renamed the desktop client to `clients/buddy.py` (the panel still serves `/clients/dani.py` for old commands). It
  uses whatever name the buddy has, can listen for that name with an always-open microphone (local Vosk recognition;
  only the sentence that names it is sent), toggled live from the Buddy settings or forced with `--wake` /
  `--no-wake`, and now exits cleanly on Ctrl+C in Windows. The push-to-talk beep no longer waits for a new audio stream.
- Speech to text runs on the GPU (Vulkan build of whisper.cpp) when the selected model fits in the free VRAM next to the
  text model, falling back to the CPU with the small model.
- Buddy settings are split into tabs (General, Instructions, Voice, Connections, Memory).
- Added a dedicated Open-Meteo weather tool for current conditions and a two-day forecast. General web search now
  allows one retry after a network failure and uses a six-second timeout, while still capping successful searches.
- Buddy conversations are now persisted, selectable and deletable, with one active conversation shared by the web UI
  and `buddy.py`; turns sent with the global PC shortcut appear live in the browser. Audio and ephemeral location are
  never stored, and `contexto_usuario` is redacted from the tool audit log.
- Replaced the invasive global Space push-to-talk binding with a per-device keyboard or side-mouse-button shortcut
  recorder and a choice of Buddy-only or whole-panel scope. Unsafe bare keys are rejected for the whole panel and
  interactive controls are never intercepted.
  Background tabs now close their reminder event stream so several open tabs cannot exhaust the browser connection pool.
- Documented the experimental Buddy installation from a fresh clone, its system dependencies, the exact Hugging Face
  audio model sources, sizes, licenses and verified hashes; the voice installer now diagnoses every required command.
- Updated the bilingual README screenshots to show the Buddy using its time and web-search tools; screenshot language
  selection is now deterministic for reproducible captures.

## 0.2.0 (2026-11-23)

The Buddy and WSL2. / El Compañero y WSL2.

- WSL2 support, tested on an RTX 2060 Max-Q with CUDA (guide in `docs/wsl.md`): `nvidia-smi` is also looked up in
  `/usr/lib/wsl/lib`, KoboldCpp defaults to `--usecuda` with NVIDIA and `--usehipblas` with AMD, AI mode is hidden,
  the memory clock helper is left out of the units, and models on the Windows drive (`/mnt/<letter>`) get a warning
  because they load several times slower.
- Configurable client ports: `LLAMA_CHAT_PORT` and `LLAMA_IMG_PORT` (8080 and 5001 by default), as environment
  variables or in `paths.env`. The measurement tools in `tools/bench/` use the same port.
- The panel, the on-demand proxy and KoboldCpp also listen on IPv6 (the `.local` name is published over IPv6 and
  Windows prefers it).
- Per-image-model defaults for steps, CFG and sampler with `--gendefaults '<JSON>'` in the extra options; KoboldCpp
  only fills in what the request leaves out, unless `--gendefaultsoverwrite` is added.
- The AI mode dashboard shows the disks and what they are reading, the GPU's kWh per day (with its cost if the kWh
  price is set in Settings), the services with their uptime, and the network: IP, connected clients and the last
  request.
- Panel status queries no longer load again a model that is being unloaded.
- The buddy speaks on its own: timers and reminders (a new `avisar` tool), homelab warnings (temperature,
  failed services, RAM, disk) and a morning summary, delivered to the web panel and `clients/dani.py` through
  server-sent events; only the client used last plays the audio. `POST /api/buddy/say` for automations.
- Optional panel password (Setup tab, Access): browser login with a 30-day session, named tokens for programs
  (`dani.py --token`, OpenAI clients), no password asked from the machine itself or through an SSH tunnel, and
  slower answers after each failed login. Only hashes are stored.
- Talk to the buddy from any tab (space bar and a floating button) and from any program on your PC with
  `clients/dani.py`, a push-to-talk client with a global key that the panel serves at `/clients/dani.py`.
- Audio engine (CPU) next to text and images: an Audio models tab to pick the Whisper model and the Piper voice
  (with a preview), and OpenAI-compatible `/v1/audio/speech` and `/v1/audio/transcriptions` on the panel port.
- Buddy tab: an agent with personality, memory and tools on the loaded text model, with voice (whisper.cpp to
  listen, Piper to speak, both on the CPU) and `scripts/install-voice.sh` to set it up without sudo.
- Optional on-demand start: the panel serves ports 8080 and 5001 and starts text or images depending on what each
  client asks for, after the requests in progress finish; requests for a model that does not exist go to the
  loaded one. Optional idle unload after N minutes. Both in the Setup tab.
- Named saved prompts per model (for example `cline` and `voice`), preloaded into llama-server's RAM prompt cache
  when the model loads: switching clients costs ~1 s instead of ~1 min.
- Global settings with the same controls as the per-model ones.
- The AI mode dashboard is bilingual (it follows the language used in the web panel) and its prompt/context bars
  move smoothly instead of jumping one 2048-token batch at a time.
- Install: lingering on (the panel starts at boot), messages in the system language, custom port kept in the unit.
- Fixes: empty paths in `paths.env`, personal default folder, AI mode button hidden when not installed, saved
  prompts no longer invalidated when equivalent settings are reordered.

## 0.2.0-rc.1 (2026-11-17)

Experimental companion prerelease. / Prerelease experimental del Compañero.

It contained the Buddy features later released in 0.2.0, plus the web search and client context listed
under Unreleased.

## 0.1.0 (2026-11-16)

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
