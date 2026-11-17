#!/usr/bin/env bash
# Voz del compañero (pestaña Compañero): whisper.cpp para entender y Piper para hablar. NO necesita sudo.
#
#   ~/llama-panel/scripts/install-voice.sh
#
# Hace falta antes: ffmpeg, cmake, un compilador de C++ y python3-venv
#   (Ubuntu/Debian: sudo apt install ffmpeg cmake build-essential python3-venv)
#
# Instala:
#   ~/whisper.cpp                                   whisper.cpp $WHISPER_VERSION compilado para el CPU (la GPU es del modelo)
#   ~/.local/share/llama-panel/voice/               modelo de Whisper, voz de Piper y el venv de Piper
#
# Todo corre en el CPU: Whisper small transcribe 4 s de audio en ~0.6 s (i7-10700, ventana ajustada al largo).
# Otro modelo o voz: WHISPER_MODEL / PIPER_VOICE en ~/.config/llama-panel/paths.env.
set -euo pipefail

WHISPER_VERSION=v1.9.5
PIPER_VERSION=1.8.0
WHISPER_DIR=${WHISPER_DIR:-$HOME/whisper.cpp}
VOICE_DIR=$HOME/.local/share/llama-panel/voice
HF=https://huggingface.co
# archivo  url  sha256 (verificado contra la API de Hugging Face)
FILES=(
  "ggml-small-q8_0.bin $HF/ggerganov/whisper.cpp/resolve/main/ggml-small-q8_0.bin 49c8fb02b65e6049d5fa6c04f81f53b867b5ec9540406812c643f177317f779f"
  "es_AR-daniela-high.onnx $HF/rhasspy/piper-voices/resolve/main/es/es_AR/daniela/high/es_AR-daniela-high.onnx 7ceb1fc0dab349418c5b54a639ae9ee595212d7c9ea422220d8419163d5cc985"
  "es_AR-daniela-high.onnx.json $HF/rhasspy/piper-voices/resolve/main/es/es_AR/daniela/high/es_AR-daniela-high.onnx.json -"
)

need() { command -v "$1" >/dev/null || { echo "Falta $1. $2"; exit 1; }; }
need ffmpeg "sudo apt install ffmpeg"
need cmake "sudo apt install cmake build-essential"
python3 -c "import ensurepip" 2>/dev/null || { echo "Falta python3-venv: sudo apt install python3-venv"; exit 1; }

# whisper.cpp
if [ ! -x "$WHISPER_DIR/build/bin/whisper-cli" ]; then
  echo "== whisper.cpp $WHISPER_VERSION en $WHISPER_DIR"
  mkdir -p "$WHISPER_DIR"
  curl -fsSL --retry 5 "https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/refs/tags/$WHISPER_VERSION" \
    | tar -xz -C "$WHISPER_DIR" --strip-components=1
  cmake -S "$WHISPER_DIR" -B "$WHISPER_DIR/build" -DCMAKE_BUILD_TYPE=Release >/dev/null
  cmake --build "$WHISPER_DIR/build" -j "$(nproc)" --target whisper-cli >/dev/null
fi

# modelos
mkdir -p "$VOICE_DIR"
for f in "${FILES[@]}"; do
  read -r name url sha <<<"$f"
  if [ ! -f "$VOICE_DIR/$name" ]; then
    echo "== $name"
    curl -fsSL --retry 5 -o "$VOICE_DIR/$name.part" "$url"
    if [ "$sha" != "-" ] && [ "$(sha256sum "$VOICE_DIR/$name.part" | cut -d' ' -f1)" != "$sha" ]; then
      rm -f "$VOICE_DIR/$name.part"; echo "sha256 distinto en $name: no se instala"; exit 1
    fi
    mv "$VOICE_DIR/$name.part" "$VOICE_DIR/$name"
  fi
done

# Piper en su propio venv (el panel usa el Python del sistema, sin dependencias)
if [ ! -x "$VOICE_DIR/piper-venv/bin/python" ]; then
  echo "== Piper $PIPER_VERSION"
  python3 -m venv "$VOICE_DIR/piper-venv"
  "$VOICE_DIR/piper-venv/bin/pip" install -q "piper-tts==$PIPER_VERSION"
fi

echo "== prueba"
"$VOICE_DIR/piper-venv/bin/python" -c "
import io, wave
from piper import PiperVoice
v = PiperVoice.load('$VOICE_DIR/es_AR-daniela-high.onnx')
buf = io.BytesIO()
with wave.open(buf, 'wb') as w:
    v.synthesize_wav('Hola, ya puedo hablar.', w)
print('Piper: ok (%d KB de audio)' % (len(buf.getvalue()) // 1024))"
"$WHISPER_DIR/build/bin/whisper-cli" --help >/dev/null 2>&1 && echo "whisper.cpp: ok"
echo "Listo. Abrí la pestaña Compañero del panel (el micrófono necesita https o localhost: túnel SSH)."
