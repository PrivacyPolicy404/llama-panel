"""Captura del panel con Firefox sin pantalla (WebDriver), para revisar cambios de la interfaz por SSH.

Uso: python3 tools/ui_shot.py SALIDA.png [URL] [--lang es|en] [--js "codigo"] [--wait SEG] [--click SELECTOR ...]
  - Espera a que la pagina dibuje los modelos (o 15 s), ejecuta los --click en orden y guarda la captura.
  - --js: imprime lo que devuelve (ej. "return document.querySelectorAll('.opt.bad').length").
Usa el geckodriver del snap de Firefox; la captura se escribe primero en ~/snap/firefox/common (el snap no ve /tmp).
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

PORT = 4445


def call(method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())["value"]


def main():
    args = sys.argv[1:]
    out = args.pop(0)
    url = args.pop(0) if args and not args[0].startswith("--") else "http://127.0.0.1:8090/#text"
    js, clicks, lang, wait_after, width, height = None, [], None, 0, 1400, 2000
    while args:
        a = args.pop(0)
        if a == "--js":
            js = args.pop(0)
        elif a == "--lang":
            lang = args.pop(0)
        elif a == "--wait":
            wait_after = float(args.pop(0))
        elif a == "--click":
            clicks.append(args.pop(0))
        elif a == "--size":
            width, height = (int(x) for x in args.pop(0).split("x"))
    gd = subprocess.Popen(["firefox.geckodriver", "--port", str(PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                call("GET", "/status"); break
            except Exception:
                time.sleep(0.2)
        sid = call("POST", "/session", {"capabilities": {"alwaysMatch": {"moz:firefoxOptions": {"args": ["-headless"]}}}})["sessionId"]
        try:
            call("POST", f"/session/{sid}/window/rect", {"width": width, "height": height})
            call("POST", f"/session/{sid}/url", {"url": url})
            if lang:
                call("POST", f"/session/{sid}/execute/sync", {"script":
                     "localStorage.setItem('llama-panel-lang', arguments[0]); return true", "args": [lang]})
                call("POST", f"/session/{sid}/refresh", {})
            for _ in range(30):
                n = call("POST", f"/session/{sid}/execute/sync",
                         {"script": "return document.querySelectorAll('.model, .img-card, tr').length", "args": []})
                if n:
                    break
                time.sleep(0.5)
            time.sleep(1.5)
            for sel in clicks:
                call("POST", f"/session/{sid}/execute/sync",
                     {"script": "const e = document.querySelector(arguments[0]); if (e) e.click(); return !!e", "args": [sel]})
                time.sleep(0.8)
            if js:
                print(json.dumps(call("POST", f"/session/{sid}/execute/sync", {"script": js, "args": []}), ensure_ascii=False))
            if wait_after:
                time.sleep(wait_after)
            png = base64.b64decode(call("GET", f"/session/{sid}/screenshot"))
            open(out, "wb").write(png)
        finally:
            call("DELETE", f"/session/{sid}")
    finally:
        gd.terminate()


if __name__ == "__main__":
    main()
