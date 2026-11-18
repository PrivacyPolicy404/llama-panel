"""Configuración local de Brave Search para las herramientas del compañero.

La clave nunca forma parte de settings.json ni se devuelve a la interfaz. El navegador sólo puede reemplazarla o
borrarla; las búsquedas se hacen desde este backend y autentican con un header, no con la URL.
"""
import os

from . import config as C

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


def token_file():
    return C.BUDDY_DIR / "brave-search.token"


def token():
    try:
        return token_file().read_text().strip()
    except OSError:
        return ""


def configured():
    return bool(token())


def save_token(value):
    C.BUDDY_DIR.mkdir(parents=True, exist_ok=True)
    f = token_file()
    f.touch(mode=0o600)
    os.chmod(f, 0o600)
    f.write_text(value.strip() + "\n")
