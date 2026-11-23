"""¿El vocabulario (--prompt de Whisper) ayuda? Corpus sintético con Piper y su transcripción exacta.

Uso:
  python3 tools/bench/whisper_vocab.py                  # todas las voces de Piper y las tres condiciones
  python3 tools/bench/whisper_vocab.py --voices 1 --conds limpio

Frases con nombres propios y jerga (lugares, mascotas, dispositivos, modelos) dichas por las voces de Piper que haya
en las carpetas de audio, en tres condiciones: limpio, ruido (ruido rosa a -28 dB) y micrófono malo (banda
telefónica, 15 % más rápido y ruido). Cada audio se transcribe sin prompt y con el vocabulario, y se mide:
- términos: cuántos nombres del vocabulario aparecen bien escritos (sin importar mayúsculas ni tildes)
- WER: palabras mal sobre el total (distancia de edición por palabras, sin puntuación ni tildes)
- fugas: con silencio y con ruido solo, ¿Whisper «escucha» el vocabulario? (el riesgo conocido del --prompt)
Por defecto en el CPU (no toca la GPU); --whisper-cli y --model para probar otro; --phonon URL compara con un
`fermion serve phonon-2.5` (Parakeet, el vocabulario va como hotwords). Resultados en ~/llama-panel-results/<fecha>-whisper-vocab/.
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.request
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from llama_panel import audio_models as AM  # noqa: E402
from llama_panel import voice  # noqa: E402

VOCAB = ["Dani", "Mar del Plata", "Chascomús", "Villa Gesell", "Necochea", "Tandil", "Ramón", "Toby", "quincho",
         "Home Assistant", "Flash-Next", "RX 580", "Luna"]
SENTENCES = [
    ("Dani, decime el clima en Mar del Plata.", ["Dani", "Mar del Plata"]),
    ("¿Va a llover mañana en Chascomús?", ["Chascomús"]),
    ("¿Qué temperatura hace en Villa Gesell?", ["Villa Gesell"]),
    ("Fijate si hay viento en Necochea.", ["Necochea"]),
    ("¿Cómo está el clima en Tandil?", ["Tandil"]),
    ("Recordame darle de comer a Toby a las nueve.", ["Toby"]),
    ("Mi gato se llama Ramón y es medio loco.", ["Ramón"]),
    ("Prendé la luz del quincho.", ["quincho"]),
    ("Abrí Home Assistant y apagá todo.", ["Home Assistant"]),
    ("¿Está cargado Flash-Next en la placa?", ["Flash-Next"]),
    ("¿Cómo anda la RX 580, Dani?", ["RX 580", "Dani"]),
    ("Sacá a pasear a Luna después de comer.", ["Luna"]),
]
CONDS = {
    "limpio": [],
    "ruido": ["-filter_complex", "anoisesrc=color=pink:amplitude=0.04:r=16000[n];[0:a][n]amix=inputs=2:duration=first"],
    "mic-malo": ["-filter_complex", "[0:a]highpass=f=300,lowpass=f=3400,atempo=1.15[v];"
                 "anoisesrc=color=pink:amplitude=0.03:r=16000[n];[v][n]amix=inputs=2:duration=first"],
}


def norm(s):
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def wer(ref, hyp):
    r, h = norm(ref).split(), norm(hyp).split()
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)] / max(1, len(r))


def has_term(text, term):
    return f" {norm(term)} " in f" {norm(text)} "


def synth(voice_path, text, out):
    sp = voice.Speaker()
    sp.fixed, sp.track = str(voice_path), False
    try:
        out.write_bytes(sp.say(text))
    finally:
        sp.stop()


def phonon_transcribe(url, audio, prompt=None, lam=None):
    """Transcribe con un `fermion serve` (ruta de OpenAI); el vocabulario va como hotwords. Devuelve (texto, None)."""
    b = uuid.uuid4().hex
    parts = [("model", "FermionResearch/Phonon-2.5"), ("response_format", "json")]
    if prompt:
        parts.append(("hotwords", prompt.rstrip(".")))
        if lam is not None:
            parts.append(("hotword_lambda", str(lam)))
    body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in parts)
    body += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
             f"Content-Type: audio/wav\r\n\r\n").encode() + audio + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(url.rstrip("/") + "/v1/audio/transcriptions", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["text"].strip(), None


def degrade(src, cond, out):
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(src)] + CONDS[cond] + ["-ar", "16000", "-ac", "1", str(out)]
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--voices", type=int, default=0, help="cuántas voces (0 = todas)")
    ap.add_argument("--conds", default=",".join(CONDS))
    ap.add_argument("--prompt", default=None, help="vocabulario a probar (por defecto, VOCAB separado por comas)")
    ap.add_argument("--out", default=str(Path.home() / "llama-panel-results" / f"{date.today()}-whisper-vocab"))
    ap.add_argument("--whisper-cli", help="otro whisper-cli (por ejemplo ~/whisper.cpp/build-vulkan/bin/whisper-cli)")
    ap.add_argument("--model", help="otro modelo de Whisper (por defecto el elegido en el panel)")
    ap.add_argument("--phonon", metavar="URL", help="usar un `fermion serve` en vez de Whisper (http://127.0.0.1:8011)")
    ap.add_argument("--hotword-lambda", type=float, help="con --phonon: cuánto empujan los hotwords (el servidor usa 2)")
    ap.add_argument("--min-ac", type=int, default=384, help="ventana mínima de audio (turbo repite con 384)")
    args = ap.parse_args()
    if args.whisper_cli:
        voice.C.WHISPER_CLI = str(Path(args.whisper_cli).expanduser())
    if args.model:
        sel = AM.selected
        AM.selected = lambda: {**sel(), "stt": str(Path(args.model).expanduser())}
    ctx = voice.audio_ctx
    voice.audio_ctx = lambda secs: ctx(secs) and max(ctx(secs), -(-args.min_ac // 64) * 64)
    if args.phonon:
        voice.transcribe = lambda audio, prompt=None: phonon_transcribe(args.phonon, audio, prompt, args.hotword_lambda)
    prompt = args.prompt or ", ".join(VOCAB) + "."
    voices = [Path(v["path"]) for v in AM.list_audio_models()["tts"]] if hasattr(AM, "list_audio_models") else []
    if not voices:
        voices = sorted(Path.home().glob("models-nvme/piper/*.onnx"))
    if args.voices:
        voices = voices[:args.voices]
    conds = args.conds.split(",")
    rows, t0 = [], time.time()
    with tempfile.TemporaryDirectory(prefix="whisper-vocab-") as d:
        d = Path(d)
        for vp in voices:
            for i, (text, terms) in enumerate(SENTENCES):
                raw = d / f"{vp.stem}-{i}.wav"
                synth(vp, text, raw)
                for cond in conds:
                    wav = d / f"{vp.stem}-{i}-{cond}.wav"
                    degrade(raw, cond, wav)
                    audio = wav.read_bytes()
                    row = {"voice": vp.stem, "cond": cond, "ref": text, "terms": terms}
                    for label, p in (("sin", None), ("con", prompt)):
                        s0 = time.time()
                        hyp, _secs = voice.transcribe(audio, prompt=p)
                        row[label] = {"text": hyp, "wer": round(wer(text, hyp), 3),
                                      "terms_ok": sum(has_term(hyp, t) for t in terms), "s": round(time.time() - s0, 2)}
                    rows.append(row)
                    mark = "" if row["con"]["terms_ok"] >= row["sin"]["terms_ok"] else "  ← peor con vocabulario"
                    print(f"{vp.stem[:14]:14} {cond:8} sin: {row['sin']['text'][:46]:46} | con: {row['con']['text'][:46]}{mark}",
                          flush=True)
        # fugas: silencio y ruido solo, con el vocabulario
        leaks = []
        for name, src in (("silencio", "anullsrc=r=16000:cl=mono"), ("ruido", "anoisesrc=color=pink:amplitude=0.05:r=16000")):
            wav = d / f"{name}.wav"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src, "-t", "3", str(wav)], check=True)
            for label, p in (("sin", None), ("con", prompt)):
                hyp, _ = voice.transcribe(wav.read_bytes(), prompt=p)
                leaks.append({"audio": name, "prompt": label, "text": hyp})
    print(f"\n{len(rows)} audios en {time.time() - t0:.0f} s · vocabulario: {prompt}")
    for cond in conds + ["TOTAL"]:
        sel = [r for r in rows if cond in ("TOTAL", r["cond"])]
        n_terms = sum(len(r["terms"]) for r in sel)
        for label in ("sin", "con"):
            ok = sum(r[label]["terms_ok"] for r in sel)
            w = sum(r[label]["wer"] for r in sel) / len(sel)
            s = sum(r[label]["s"] for r in sel) / len(sel)
            print(f"  {cond:8} {label}: términos {ok:3}/{n_terms} ({100 * ok / n_terms:4.0f} %) · WER {100 * w:4.1f} % · {s:.2f} s")
    for x in leaks:
        print(f"  fuga? {x['audio']:8} {x['prompt']}: {x['text']!r}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{int(time.time())}.json").write_text(json.dumps({"prompt": prompt, "rows": rows, "leaks": leaks},
                                                              ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
