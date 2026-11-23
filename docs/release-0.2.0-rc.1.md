# v0.2.0-rc.1 — Experimental Companion

First experimental release of the voice-enabled Buddy. It is a prerelease: use it for testing and report issues.
The base Buddy is stable since v0.2.0 (`main`); web search, weather and saved conversations are still experimental.

Primera versión experimental del Compañero con voz. Es una prerelease: usala para probar y reportar problemas.
El Compañero base es estable desde la v0.2.0 (`main`); la búsqueda web, el clima y las conversaciones guardadas siguen siendo experimentales.

## Install / Instalación

```bash
git clone --branch v0.2.0-rc.1 https://github.com/PrivacyPolicy404/llama-panel.git ~/llama-panel
cd ~/llama-panel
python3 panel.py --install
sudo apt install curl ffmpeg cmake build-essential python3-venv  # Ubuntu/Debian
scripts/install-voice.sh
```

The voice installer runs as the current user; only the system package command needs `sudo`. The browser microphone
requires HTTPS or `localhost` (an SSH tunnel works).

El instalador de voz corre como el usuario actual; sólo la instalación de paquetes del sistema necesita `sudo`. El
micrófono del navegador requiere HTTPS o `localhost` (sirve un túnel SSH).

## Included / Incluye

- Voice conversation using whisper.cpp for speech recognition and Piper for streaming speech synthesis.
- Editable personality, persistent memory and an audited tool system with per-tool risk levels.
- Read-only tools for homelab status, current time, one short web search per turn and ephemeral browser context.
- Optional approximate location, disabled by default, rounded before transmission and never stored.
- Home Assistant integration restricted to an allowlist of device types; no scripts or automations.
- Timers, reminders, homelab alerts and a morning summary.
- Web push-to-talk from every tab and the optional `clients/dani.py` desktop client.
- Optional panel authentication with browser sessions and named program tokens.

## Default audio models / Modelos de audio predeterminados

The installer downloads these models from their official Hugging Face repositories and verifies their SHA-256 hashes:

| Purpose / Uso | Model / Modelo | Source / Origen | Size / Tamaño | License / Licencia |
|---|---|---|---:|---|
| Speech to text / Voz a texto | Whisper `small-q8_0` | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp/blob/main/ggml-small-q8_0.bin) | 264 MB | MIT |
| Text to speech / Texto a voz | Piper `es_AR-daniela-high` | [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices/tree/main/es/es_AR/daniela/high) | 114 MB | MIT |

## Experimental notes / Notas experimentales

- Web search and reverse geocoding depend on external read-only providers and may fail or time out.
- Approximate location must be enabled separately on each browser/device.
- Audio performance depends heavily on the CPU; the defaults were measured on an i7-10700.
- The panel is intended for a trusted local network. Do not expose its model ports directly to the internet.

**Full changelog / Cambios completos:**
[v0.1.0...v0.2.0-rc.1](https://github.com/PrivacyPolicy404/llama-panel/compare/v0.1.0...v0.2.0-rc.1)
