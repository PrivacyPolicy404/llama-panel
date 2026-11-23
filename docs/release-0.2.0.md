Second release of **llama-panel**. Besides text and images, the panel now runs **the Buddy**: an agent with
personality, memory, tools and voice on whatever text model is loaded. It also runs under **WSL2** with NVIDIA.

**What's new**
- **Buddy** tab: an agent on the loaded text model with editable personality, persistent memory and audited tools.
  Voice with whisper.cpp (listen) and Piper (speak, streaming), both on the CPU; `scripts/install-voice.sh` sets
  them up without sudo. It speaks on its own too: timers, reminders, homelab warnings and a morning summary.
- Talk to it from any tab (space bar or a floating button) or from any program on your PC with `clients/dani.py`,
  a push-to-talk client that the panel serves itself.
- Home Assistant: the Buddy sees and controls lights, plugs and scenes (an allowlist of device types; no scripts
  or automations).
- **Audio engine** next to text and images: an Audio models tab and OpenAI-compatible `/v1/audio/speech` and
  `/v1/audio/transcriptions` on the panel port.
- **On-demand start**, optional: the panel serves ports 8080 and 5001 and swaps text and images depending on what
  each client asks for, after the requests in progress finish. Optional idle unload.
- **Optional password** for the panel (browser sessions and named tokens for programs). It is never asked from the
  machine itself or through an SSH tunnel.
- Named saved prompts per model, preloaded into llama-server's RAM prompt cache: switching clients costs ~1 s
  instead of ~1 min.
- **WSL2**, tested on an RTX 2060 Max-Q with CUDA ([docs/wsl.md](https://github.com/PrivacyPolicy404/llama-panel/blob/main/docs/wsl.md)).
  The text and image ports can be changed in `paths.env` when Windows or Docker already use them.
- IPv6 for the panel, the proxy and KoboldCpp; per-image-model defaults with `--gendefaults`; a fuller AI mode
  dashboard (disks, GPU kWh, services, network).

The full list is in [CHANGELOG.md](https://github.com/PrivacyPolicy404/llama-panel/blob/main/CHANGELOG.md).

**Not in this release:** the web search, weather, saved conversations and `clients/buddy.py` with wake word from
the experimental line ([v0.2.0-rc.1](https://github.com/PrivacyPolicy404/llama-panel/releases/tag/v0.2.0-rc.1) and the
`experimental/companion` branch). They will come in a later release once they are validated.

**Tested on** AMD RX 580 8 GB with Vulkan (RADV) on Kubuntu, and an NVIDIA RTX 2060 Max-Q with CUDA on WSL2 (Ubuntu).

**Security:** the panel is meant for a trusted local network. The model ports (8080 and 5001) have no
authentication: do not expose them to the internet.

**Install**
```bash
git clone https://github.com/PrivacyPolicy404/llama-panel.git ~/llama-panel
cd ~/llama-panel && python3 panel.py --install
# optional, voice for the Buddy (Ubuntu/Debian packages first):
sudo apt install ffmpeg cmake build-essential python3-venv
scripts/install-voice.sh
```

Notas en castellano: el panel ahora trae el Compañero (agente con voz, memoria, herramientas y Home Assistant), el
motor de audio, el encendido a demanda, contraseña opcional y soporte para WSL2 con NVIDIA. La interfaz está en
castellano y en inglés.
