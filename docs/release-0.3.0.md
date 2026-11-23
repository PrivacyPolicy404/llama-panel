The Buddy, complete: everything that was in the experimental line ([v0.2.0-rc.1](https://github.com/PrivacyPolicy404/llama-panel/releases/tag/v0.2.0-rc.1)
and the `experimental/companion` branch) is now in `main`.

**What's new**
- **Web search and weather**: one short web search per turn (Brave Search with your own key, or DuckDuckGo
  without one; results treated as untrusted content) and a dedicated Open-Meteo weather tool with a two-day forecast.
- **Saved conversations**, selectable and deletable, with one active conversation shared by the web panel and
  `buddy.py`. Audio and location are never stored.
- **`clients/buddy.py`** replaces `dani.py` (the old address still works): it uses the Buddy's name, exits cleanly
  on Windows and can listen for that name (see below).
- **Better hearing**: Whisper gets a vocabulary built from the Buddy's name, your area, Home Assistant, its memory
  and the corrections it learns. It runs on the GPU when it fits next to the text model.
- **Instructions in sections** (identity, how it talks, environment, when to use tools) with a "Save and test"
  button that measures how well it picks its tools.
- **Faster long chats**: the history is trimmed in steps, so the prompt is not reprocessed on every turn, and the
  fixed prompt is preloaded when the model loads again.
- A push-to-talk shortcut you record yourself (keyboard or side mouse buttons), Buddy settings in tabs, and a switch
  to turn the morning summary off without losing its time.

The full list is in [CHANGELOG.md](https://github.com/PrivacyPolicy404/llama-panel/blob/main/CHANGELOG.md).

**Good to know**
- **Listening for the name is optional and off by default.** When it is on, `buddy.py` keeps the microphone open.
  Recognition of the name runs locally (Vosk) and only the sentence that names the Buddy is sent to the panel. It
  can be turned on and off from the Buddy settings or with `--wake` / `--no-wake`.
- **Whisper on the GPU** was tested only on an RX 580 with the Vulkan build of whisper.cpp. When the model does not
  fit in the free VRAM next to the text model, it falls back to the CPU with the small model.
- Web search and the weather depend on external services and may fail or time out. Approximate location is off by
  default and has to be enabled on each device.

**Tested on** AMD RX 580 8 GB with Vulkan (RADV) on Kubuntu, and an NVIDIA RTX 2060 Max-Q with CUDA on WSL2 (Ubuntu).

**Security:** the panel is meant for a trusted local network. The model ports (8080 and 5001) have no
authentication: do not expose them to the internet.

**Install**
```bash
git clone https://github.com/PrivacyPolicy404/llama-panel.git ~/llama-panel
cd ~/llama-panel && python3 panel.py --install
# optional, voice for the Buddy (Ubuntu/Debian packages first):
sudo apt install curl ffmpeg cmake build-essential python3-venv
scripts/install-voice.sh
```

Notas en castellano: el Compañero queda completo en `main`, con búsqueda web y clima, conversaciones guardadas,
`buddy.py` con escucha por nombre (opcional y apagada por defecto; el nombre se reconoce en la máquina con Vosk),
Whisper con vocabulario propio y en la GPU cuando entra, e instrucciones por secciones que se pueden probar.
