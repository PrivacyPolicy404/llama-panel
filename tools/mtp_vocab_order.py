#!/usr/bin/env python3
"""Ordena el vocabulario de Flash-Next por frecuencia de uso, para recortar la salida del borrador MTP.

Tokeniza con el modelo cargado en el router varios corpus (repos y conversaciones locales, documentación del sistema
y oraciones de Leipzig en castellano e inglés), suma la frecuencia relativa de cada token con un peso por corpus y
escribe {"order": [ids, del más al menos frecuente], "stats": ...}. Los tokens especiales van al frente.
Al final imprime la cobertura sobre las respuestas guardadas en ~/llama-panel-results (no usadas para ordenar).

    mtp_vocab_order.py SALIDA.json CORPUS_DIR

CORPUS_DIR tiene los corpus de Leipzig (https://wortschatz.uni-leipzig.de/en/download) descomprimidos:
spa_news_2023_100K, spa_wikipedia_2021_100K, eng_news_2023_100K y eng_wikipedia_2016_100K.
El resultado lo usa `gguf_mtp_d2t.py`.
"""
import collections
import glob
import json
import os
import re
import sys
import urllib.request

MODEL = "Qwen3.8-Flash-Next-GSQ-RCO-abliterated-Q2_0-hcq8"
N_VOCAB = 248320
FIRST_SPECIAL = 248000  # desde acá son tokens de control
LEIPZIG = (("spa_news_2023_100K", 2.0), ("spa_wikipedia_2021_100K", 1.5),
           ("eng_news_2023_100K", 1.0), ("eng_wikipedia_2016_100K", 1.0))
HOME = os.path.expanduser("~")


def tokenize(text):
    out = []
    for i in range(0, len(text), 300000):
        req = urllib.request.Request("http://127.0.0.1:8080/tokenize", headers={"Content-Type": "application/json"},
            data=json.dumps({"model": MODEL, "content": text[i:i + 300000]}).encode())
        out += json.loads(urllib.request.urlopen(req, timeout=600).read())["tokens"]
    return out


def read(paths, limit):
    buf, n = [], 0
    for p in paths:
        try:
            t = open(p, errors="ignore").read()
        except OSError:
            continue
        buf.append(t)
        n += len(t)
        if n > limit:
            break
    return "\n".join(buf)[:limit]


def files(*patterns):
    return sorted(p for pat in patterns for p in glob.glob(pat, recursive=True))


def conversations():
    out = []
    for p in sorted(glob.glob(HOME + "/.claude/projects/*/*.jsonl")):
        for line in open(p, errors="ignore"):
            try:
                content = json.loads(line).get("message", {}).get("content")
            except (ValueError, AttributeError):
                continue
            if isinstance(content, str):
                out.append(content)
            elif isinstance(content, list):
                out += [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
    return "\n".join(out)[:6_000_000]


def main():
    out_path, corpus_dir = sys.argv[1:3]
    sources = {
        "panel": (1.0, read(files(HOME + "/llama-panel/**/*.md", HOME + "/llama-panel/**/*.py",
            HOME + "/llama-panel/static/**/*.js"), 3_000_000)),
        "codigo": (1.5, read(files(HOME + "/llama.cpp/docs/**/*.md") + files(HOME + "/llama.cpp/tools/server/*.cpp")
            + files(HOME + "/llama.cpp/common/*.cpp") + files(HOME + "/llama.cpp/**/*.py")[:200], 6_000_000)),
        "conversaciones": (3.0, conversations()),
        "sysdocs": (0.5, read(files("/usr/share/doc/**/*.txt", "/usr/share/doc/**/README*"), 3_000_000)),
    }
    for name, weight in LEIPZIG:
        text = open(f"{corpus_dir}/{name}/{name}-sentences.txt", errors="ignore").read()
        sources[name] = (weight, re.sub(r"(?m)^\d+\t", "", text))

    score, stats = collections.Counter(), {}
    for name, (weight, text) in sources.items():
        tokens = tokenize(text)
        count = collections.Counter(tokens)
        stats[name] = {"chars": len(text), "tokens": len(tokens), "distintos": len(count), "peso": weight}
        for token, n in count.items():
            score[token] += weight * n / len(tokens)
        print(name, stats[name], file=sys.stderr, flush=True)

    order = [t for t, _ in score.most_common()]
    special = [i for i in range(FIRST_SPECIAL, N_VOCAB) if i not in score]
    seen = set(order) | set(special)
    order = special + order + [i for i in range(N_VOCAB) if i not in seen]
    assert sorted(order) == list(range(N_VOCAB))
    json.dump({"order": order, "stats": stats, "n_scored": len(score)}, open(out_path, "w"))

    held = set()
    for p in glob.glob(HOME + "/llama-panel-results/**/*.json", recursive=True):
        try:
            d = json.load(open(p))
        except (ValueError, OSError):
            continue
        if isinstance(d, dict) and isinstance(d.get("content"), str) and len(d["content"]) > 200:
            held.add(d["content"])
    tokens = tokenize("\n".join(held))
    print("ids con puntaje:", len(score), "| respuestas guardadas:", len(tokens), "tokens")
    for k in (16384, 24576, 32768, 49152, 65536):
        top = set(order[:k])
        print(f"K={k}: {sum(t in top for t in tokens) / len(tokens) * 100:.2f} %"
              f"   (primeros ids: {sum(t < k for t in tokens) / len(tokens) * 100:.2f} %)")


if __name__ == "__main__":
    main()
