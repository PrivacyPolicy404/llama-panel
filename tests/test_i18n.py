"""Que todo texto de la interfaz y de los mensajes del servidor tenga su traducción al inglés."""
import ast
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

from llama_panel import i18n

ROOT = Path(__file__).resolve().parent.parent
norm = lambda s: re.sub(r"\s+", " ", s).strip()
# se escriben igual en los dos idiomas
SAME = {"Hardware", "VRAM", "RAM", "Error", "Panel", "ES", "EN", "llama-server · router", "KoboldCpp · StableUI",
        "Idioma / Language", "Prompt tok/s", "MTP", "Flash attention", "auto"}


def js_dictionary():
    src = (ROOT / "static/js/i18n.js").read_text()
    body = src[src.index("export const EN = {"):]
    return {norm(m[1] if m[1] is not None else m[2])
            for m in re.finditer(r"^\s*(?:'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\")\s*:", body, re.M)}


def js_keys():
    keys = set()
    for f in (ROOT / "static/js").glob("*.js"):
        s = f.read_text()
        keys |= {norm(m.replace("\\'", "'")) for m in re.findall(r"\b(?:t|tr)\(\s*'((?:[^'\\]|\\.)*)'", s)}
    keys |= set(re.findall(r"\['(?:good|warning|critical|)', '([^']+)'\]", (ROOT / "static/js/util.js").read_text()))
    return keys


class _Html(HTMLParser):
    def __init__(self):
        super().__init__()
        self.keys, self.stack, self.html, self.skip = set(), [], 0, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.keys |= {norm(a[k]) for k in ("title", "placeholder", "data-confirm", "aria-label") if a.get(k)}
        raw, code = "data-i18n-html" in a, tag in ("script", "style", "code", "pre")
        self.html += raw
        self.skip += code
        self.stack.append((raw, code))

    def handle_endtag(self, tag):
        if self.stack:
            raw, code = self.stack.pop()
            self.html -= raw
            self.skip -= code

    def handle_data(self, data):
        if not self.html and not self.skip and re.search(r"[A-Za-zÁÉÍÓÚáéíóúñ]", data):
            self.keys.add(norm(data))


def html_keys():
    text = (ROOT / "static/index.html").read_text()
    p = _Html()
    p.feed(text)
    p.keys |= {norm(m[2]) for m in re.finditer(r"<(\w+)[^>]*data-i18n-html[^>]*>(.*?)</\1>", text, re.S)}
    return p.keys


def py_keys():
    keys = set()
    for f in (ROOT / "llama_panel").glob("*.py"):
        for m in re.finditer(r"\b_\(((?:\s*\"(?:[^\"\\]|\\.)*\")+)", f.read_text()):
            keys.add(ast.literal_eval("(" + m[1] + ")"))
    return keys


def field_keys():
    """Textos de los campos de cada modelo: la interfaz los dibuja desde la API y los traduce con t()."""
    from llama_panel.text_models import FIELDS
    keys = set()
    for f in FIELDS:
        keys |= {norm(f[k]) for k in ("label", "hint", "placeholder") if f.get(k) and not f[k].isdigit()}
        keys |= {norm(label) for _, label in f.get("options", []) if not label.isdigit()}
    return keys


class Translations(unittest.TestCase):
    def test_interface(self):
        missing = sorted((js_keys() | html_keys() | field_keys()) - js_dictionary() - SAME)
        self.assertEqual(missing, [], "faltan en static/js/i18n.js")

    def test_server_messages(self):
        missing = sorted(k for k in py_keys() if k not in i18n.EN)
        self.assertEqual(missing, [], "faltan en llama_panel/i18n.py")

    def test_server_translation_and_fallback(self):
        i18n.set_lang("en")
        try:
            self.assertEqual(i18n._("{n} modelos encontrados.", n=3), "3 models found.")
            self.assertEqual(i18n._("texto sin traducir"), "texto sin traducir")
        finally:
            i18n.set_lang("es")
        self.assertEqual(i18n._("{n} modelos encontrados.", n=3), "3 modelos encontrados.")
        i18n.set_lang("fr")  # idioma desconocido: castellano
        self.assertEqual(i18n.LANG.get(), "es")


if __name__ == "__main__":
    unittest.main()
