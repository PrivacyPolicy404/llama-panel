"""Proceso de Piper para el compañero. Corre con el Python del venv de Piper (no importa nada del panel).

Uso: <venv>/bin/python tts_worker.py VOZ.onnx
Lee una línea JSON por oración ({"text": "..."}) y escribe por stdout el WAV con 4 bytes de largo adelante.
"""
import io
import json
import struct
import sys
import wave


def main():
    from piper import PiperVoice
    voice = PiperVoice.load(sys.argv[1])
    out = sys.stdout.buffer
    for line in sys.stdin:
        try:
            text = json.loads(line).get("text", "")
        except ValueError:
            text = ""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            if text:
                voice.synthesize_wav(text, wf)
            else:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(22050)
        data = buf.getvalue()
        out.write(struct.pack(">I", len(data)) + data)
        out.flush()


if __name__ == "__main__":
    main()
