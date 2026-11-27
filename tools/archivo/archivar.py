#!/usr/bin/env python3
"""Archivo de modelos en un disco aparte, por etapas (la lista está en tools/archivo/manifiesto.txt).

  python3 tools/archivo/archivar.py                          # las etapas y qué entra en cada una (sin internet)
  python3 tools/archivo/archivar.py tamanos 3 4              # tamaño real, y si los repos existen
  python3 tools/archivo/archivar.py tamanos 4 --detalle      # ídem con la lista de archivos que bajaría
  python3 tools/archivo/archivar.py buscar "SC117"           # repos de Hugging Face por descargas
  python3 tools/archivo/archivar.py bajar 1 --destino /mnt/archivo
  python3 tools/archivo/archivar.py verificar --destino /mnt/archivo

`bajar` se puede cortar (Ctrl+C, se cae la conexión, se duerme la laptop) y se retoma con el mismo comando: lo
bajado queda en un .part y sigue desde ahí. Cada archivo de Hugging Face se compara con el sha256 que publica el
repo. La revisión del repo se fija en la primera descarga: si el repo cambia después, se completa la misma.

En el archivo cada carpeta tiene:
  ARCHIVO.md     de dónde salió, revisión, fecha y la nota del manifiesto
  SHA256SUMS     compatible con `sha256sum -c SHA256SUMS`, para revisar el disco dentro de unos años sin este script
  .archivo.json  lo mismo, para el script
y en la raíz quedan INDICE.md y una copia de este script y del manifiesto (_herramientas/).

HF_TOKEN en el entorno sirve para los repos con acceso restringido (antes hay que aceptar las condiciones en la web).
Solo biblioteca estándar.
"""
import argparse
import fnmatch
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

AQUI = Path(__file__).resolve().parent
MANIFIESTO = AQUI / "manifiesto.txt"
HF = "https://huggingface.co"
EXTRAS_HF = ("README.md", "LICENSE*", "LICENCE*", "NOTICE*", "USE_POLICY*")
BLOQUE = 8 * 1024 * 1024
MARGEN_LIBRE = 2 * 1024**3  # lo que se deja libre en el disco
INTENTOS = 8
ESTADOS = ("ok", "verificar")


class ErrorArchivo(Exception):
    pass


# ---------------------------------------------------------------- manifiesto

@dataclass
class Entrada:
    etapa: int
    fuente: str
    incluir: list
    excluir: list
    carpeta: str
    estado: str
    nota: str
    linea: int = 0

    @property
    def tipo(self):
        return self.fuente.split(":", 1)[0]

    @property
    def destino(self):
        return self.fuente.split(":", 1)[1]


@dataclass
class Etapa:
    num: int
    titulo: str
    entradas: list = field(default_factory=list)


def leer_manifiesto(texto):
    etapas, carpetas = [], set()
    for n, linea in enumerate(texto.splitlines(), 1):
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        m = re.fullmatch(r"\[(\d+)\]\s*(.+)", linea)
        if m:
            num = int(m.group(1))
            if etapas and num != etapas[-1].num + 1 or not etapas and num != 1:
                raise ErrorArchivo(f"manifiesto, línea {n}: las etapas van 1, 2, 3... (vino {num})")
            etapas.append(Etapa(num, m.group(2).strip()))
            continue
        partes = [p.strip() for p in linea.split("|")]
        if len(partes) != 5:
            raise ErrorArchivo(f"manifiesto, línea {n}: tienen que ser 5 campos separados por |")
        if not etapas:
            raise ErrorArchivo(f"manifiesto, línea {n}: falta un [1] antes de la primera entrada")
        fuente, incluir, carpeta, estado, nota = partes
        tipo = fuente.split(":", 1)[0]
        if tipo not in ("hf", "local", "git", "url") or ":" not in fuente:
            raise ErrorArchivo(f"manifiesto, línea {n}: fuente desconocida {fuente!r}")
        if estado not in ESTADOS:
            raise ErrorArchivo(f"manifiesto, línea {n}: estado {estado!r} (va ok o verificar)")
        if carpeta in carpetas or carpeta.startswith("/") or ".." in Path(carpeta).parts:
            raise ErrorArchivo(f"manifiesto, línea {n}: carpeta {carpeta!r} repetida o fuera del archivo")
        carpetas.add(carpeta)
        pats = [p.strip() for p in incluir.split(",") if p.strip()]
        inc = [p for p in pats if not p.startswith("!")]
        exc = [p[1:] for p in pats if p.startswith("!")]
        etapas[-1].entradas.append(Entrada(etapas[-1].num, fuente, inc, exc, carpeta, estado, nota, n))
    return etapas


def coincide(ruta, incluir, excluir, extras=()):
    """`ruta` es relativa al repo. Los patrones se prueban contra la ruta entera y contra el nombre."""
    nombre = ruta.rsplit("/", 1)[-1]

    def hay(pats):
        return any(fnmatch.fnmatchcase(ruta, p) or fnmatch.fnmatchcase(nombre, p) for p in pats)

    if "/" not in ruta and any(fnmatch.fnmatchcase(ruta, p) for p in extras):
        return True
    return hay(incluir) and not hay(excluir)


# ---------------------------------------------------------------- Hugging Face

def _pedido(url, headers=None):
    h = {"User-Agent": "llama-panel-archivo/1"}
    token = os.environ.get("HF_TOKEN")
    if token and urllib.parse.urlparse(url).hostname == "huggingface.co":
        h["Authorization"] = f"Bearer {token}"
    h.update(headers or {})
    return urllib.request.Request(url, headers=h)


def _json(url):
    try:
        with urllib.request.urlopen(_pedido(url), timeout=60) as r:
            siguiente = None
            for parte in (r.headers.get("Link") or "").split(","):
                m = re.search(r'<([^>]+)>\s*;\s*rel="next"', parte)
                if m:
                    siguiente = m.group(1)
            return json.load(r), siguiente
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ErrorArchivo("no existe (404)") from None
        if e.code in (401, 403):
            raise ErrorArchivo(f"acceso restringido ({e.code}): aceptar las condiciones en la web y exportar HF_TOKEN") from None
        raise ErrorArchivo(f"Hugging Face respondió {e.code}") from None
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError) as e:
        raise ErrorArchivo(f"sin conexión con Hugging Face ({getattr(e, 'reason', e)})") from None


def hf_revision(repo):
    data, _ = _json(f"{HF}/api/models/{repo}")
    return data["sha"]


def hf_archivos(repo, rev):
    url = f"{HF}/api/models/{repo}/tree/{rev}?recursive=true"
    archivos = []
    while url:
        data, url = _json(url)
        for it in data:
            if it.get("type") != "file":
                continue
            lfs = it.get("lfs") or {}
            archivos.append({"ruta": it["path"], "tam": it.get("size", lfs.get("size", 0)),
                             "sha256": lfs.get("oid"), "oid": it.get("oid")})
    return archivos


def hf_url(repo, rev, ruta):
    return f"{HF}/{repo}/resolve/{rev}/{urllib.parse.quote(ruta)}"


def sha256_de(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for b in iter(lambda: f.read(BLOQUE), b""):
            h.update(b)
    return h.hexdigest()


def oid_git(ruta):
    """El sha1 que git le da a un archivo (los archivos chicos de HF no traen sha256)."""
    h = hashlib.sha1(b"blob %d\0" % ruta.stat().st_size)
    with open(ruta, "rb") as f:
        for b in iter(lambda: f.read(BLOQUE), b""):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- transferencia con retomado

def _gb(n):
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.1f} MB"


class Progreso:
    def __init__(self, nombre, total):
        self.nombre, self.total = nombre, total
        self.t0 = self.ult = time.monotonic()
        self.tty = sys.stderr.isatty()

    def __call__(self, hecho, desde, fin=False):
        ahora = time.monotonic()
        if not fin and ahora - self.ult < (1 if self.tty else 60):
            return
        self.ult = ahora
        vel = (hecho - desde) / max(ahora - self.t0, 1e-6)
        total = f"/{_gb(self.total)}" if self.total else ""
        eta = f"  faltan {int((self.total - hecho) / vel / 60)} min" if self.total and vel > 0 and not fin else ""
        txt = f"    {self.nombre}  {_gb(hecho)}{total}  {vel / 1e6:.0f} MB/s{eta}"
        print(("\r" + txt.ljust(100)) if self.tty else txt, end="\n" if fin or not self.tty else "", file=sys.stderr, flush=True)


def transferir(abrir, destino, tam, sha256=None, oid=None, intentos=INTENTOS, espera=5):
    """Copia a `destino` lo que da abrir(desde) (un lector que empieza en el byte `desde`) y devuelve el sha256.

    Escribe en destino.part y sigue desde donde quedó, entre intentos y entre corridas. `tam` puede ser None
    (no se sabe: entonces no se retoma). abrir() devuelve (lector, retoma): retoma=False si la fuente ignoró el
    rango pedido y vuelve a empezar desde 0.
    """
    part = destino.with_name(destino.name + ".part")
    destino.parent.mkdir(parents=True, exist_ok=True)
    h, hecho = hashlib.sha256(), 0
    if part.exists() and tam:
        hecho = part.stat().st_size
        if hecho > tam:
            part.unlink()
            hecho = 0
        else:
            with open(part, "rb") as f:  # el hash de lo que ya estaba (una vez por corrida)
                for b in iter(lambda: f.read(BLOQUE), b""):
                    h.update(b)
    elif part.exists():
        part.unlink()
    prog = Progreso(destino.name, tam)
    inicio = hecho
    for intento in range(intentos):
        try:
            if tam is None or hecho < tam:
                _espacio(destino.parent, (tam or 0) - hecho)
                lector, retoma = abrir(hecho)
                with lector:
                    if hecho and not retoma:
                        h, hecho, inicio = hashlib.sha256(), 0, 0
                    with open(part, "ab" if hecho else "wb") as f:
                        for b in iter(lambda: lector.read(BLOQUE), b""):
                            f.write(b)
                            h.update(b)
                            hecho += len(b)
                            prog(hecho, inicio)
            if tam is not None and hecho != tam:
                raise OSError(f"quedó en {hecho} de {tam} bytes")
            break
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError, OSError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403, 404):
                raise ErrorArchivo(f"{destino.name}: Hugging Face respondió {e.code}") from None
            if intento == intentos - 1:
                raise ErrorArchivo(f"{destino.name}: {e} (lo bajado queda en .part; correr de nuevo para seguir)") from None
            pausa = min(300, espera * 2 ** intento)
            print(f"\n    corte ({e}); reintento en {pausa} s", file=sys.stderr)
            time.sleep(pausa)
    prog(hecho, inicio, fin=True)
    final = h.hexdigest()
    if sha256 and final != sha256:
        part.unlink()
        raise ErrorArchivo(f"{destino.name}: sha256 no coincide con el del repo (se borró, correr de nuevo)")
    os.replace(part, destino)
    if oid and not sha256 and oid_git(destino) != oid:
        destino.unlink()
        raise ErrorArchivo(f"{destino.name}: no coincide con el repo (se borró, correr de nuevo)")
    return final


def _espacio(carpeta, falta):
    libre = shutil.disk_usage(carpeta).free
    if falta + MARGEN_LIBRE > libre:
        raise ErrorArchivo(f"no hay lugar: faltan {_gb(falta)} y quedan {_gb(libre)} libres en {carpeta}")


def abrir_http(url):
    def abrir(desde):
        hdr = {"Range": f"bytes={desde}-"} if desde else {}
        r = urllib.request.urlopen(_pedido(url, hdr), timeout=120)
        return r, r.status == 206
    return abrir


def abrir_local(origen):
    def abrir(desde):
        f = open(origen, "rb")
        f.seek(desde)
        return f, True
    return abrir


# ---------------------------------------------------------------- estado de cada carpeta

def leer_estado(carpeta):
    try:
        return json.loads((carpeta / ".archivo.json").read_text())
    except (OSError, ValueError):
        return {}


def guardar_estado(carpeta, entrada, estado):
    estado.update(fuente=entrada.fuente, nota=entrada.nota)
    tmp = carpeta / ".archivo.json.tmp"
    tmp.write_text(json.dumps(estado, indent=1, ensure_ascii=False))
    os.replace(tmp, carpeta / ".archivo.json")
    archivos = estado.get("archivos", {})
    (carpeta / "SHA256SUMS").write_text("".join(f"{a['sha256']}  {r}\n" for r, a in sorted(archivos.items())))
    fuente = entrada.fuente
    if entrada.tipo == "hf":
        fuente = f"{HF}/{entrada.destino} (revisión {estado.get('rev', '?')})"
    elif entrada.tipo == "git" and estado.get("rev"):
        fuente = f"{entrada.destino} (HEAD {estado['rev']})"
    filas = "".join(f"| `{r}` | {_gb(a['tam'])} | `{a['sha256']}` |\n" for r, a in sorted(archivos.items()))
    total = sum(a["tam"] for a in archivos.values())
    (carpeta / "ARCHIVO.md").write_text(
        f"# {entrada.carpeta}\n\n- Fuente: {fuente}\n- Archivado: {estado.get('fecha', '?')}\n"
        f"- Total: {_gb(total)}\n- Nota: {entrada.nota}\n\n"
        f"Verificar: `cd` acá y `sha256sum -c SHA256SUMS`.\n\n| Archivo | Tamaño | sha256 |\n|---|---|---|\n{filas}")


def ya_esta(carpeta, estado, ruta, tam, sha256=None):
    a = estado.get("archivos", {}).get(ruta)
    p = carpeta / ruta
    return bool(a and p.exists() and p.stat().st_size == tam == a["tam"] and (not sha256 or a["sha256"] == sha256))


def ahora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------- cada tipo de fuente

def plan_hf(e, rev=None):
    rev = rev or hf_revision(e.destino)
    archivos = [a for a in hf_archivos(e.destino, rev) if coincide(a["ruta"], e.incluir, e.excluir, EXTRAS_HF)]
    return rev, archivos


def plan_local(e):
    origen = Path(e.destino).expanduser()
    if not origen.is_dir():
        raise ErrorArchivo(f"no está {origen} en esta máquina")
    archivos = []
    for p in sorted(origen.rglob("*")):
        if p.is_file() and not p.name.endswith(".part"):  # sigue enlaces simbólicos: se copia el contenido
            ruta = p.relative_to(origen).as_posix()
            if coincide(ruta, e.incluir, e.excluir):
                archivos.append({"ruta": ruta, "tam": p.stat().st_size, "origen": p})
    return archivos


def bajar_hf(e, carpeta, estado):
    rev = estado.get("rev")
    if rev:
        try:
            actual = hf_revision(e.destino)
            if actual != rev:
                print(f"    el repo cambió desde la primera descarga ({rev[:8]} → {actual[:8]}): sigo con {rev[:8]}. "
                      f"Para la nueva, mover {carpeta} y bajar de nuevo.")
        except ErrorArchivo:
            pass
    rev, archivos = plan_hf(e, rev)
    if not [a for a in archivos if not coincide(a["ruta"], [], [], EXTRAS_HF)]:
        raise ErrorArchivo("ningún archivo coincide con los patrones (ver `tamanos --detalle`)")
    estado.update(rev=rev, fecha=estado.get("fecha") or ahora())
    estado.setdefault("archivos", {})
    for a in archivos:
        if ya_esta(carpeta, estado, a["ruta"], a["tam"], a["sha256"]):
            continue
        sha = transferir(abrir_http(hf_url(e.destino, rev, a["ruta"])), carpeta / a["ruta"], a["tam"], a["sha256"], a["oid"])
        estado["archivos"][a["ruta"]] = {"tam": a["tam"], "sha256": sha}
        guardar_estado(carpeta, e, estado)


def bajar_local(e, carpeta, estado, enlaces):
    estado.update(fecha=estado.get("fecha") or ahora())
    estado.setdefault("archivos", {})
    archivos = plan_local(e)
    if not archivos:
        raise ErrorArchivo("ningún archivo coincide con los patrones")
    for a in archivos:
        destino, st = carpeta / a["ruta"], a["origen"].stat()
        clave = (st.st_dev, st.st_ino)
        if ya_esta(carpeta, estado, a["ruta"], a["tam"]):
            enlaces.setdefault(clave, (destino, estado["archivos"][a["ruta"]]["sha256"]))
            continue
        if st.st_nlink > 1 and clave in enlaces and enlaces[clave][0].exists():
            # el mismo archivo con dos nombres en el origen (shard de n-gramas de Flash-Next): se guarda una vez
            otro, sha = enlaces[clave]
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.unlink(missing_ok=True)
            os.link(otro, destino)
            print(f"    {a['ruta']}: enlace duro a {otro} (no ocupa lugar)")
        else:
            sha = transferir(abrir_local(a["origen"]), destino, a["tam"])
        enlaces[clave] = (destino, sha)
        estado["archivos"][a["ruta"]] = {"tam": a["tam"], "sha256": sha}
        guardar_estado(carpeta, e, estado)


def bajar_git(e, carpeta, estado, tmp):
    origen = e.destino if "://" in e.destino else str(Path(e.destino).expanduser())
    nombre = Path(e.carpeta).name + ".bundle"
    if nombre in estado.get("archivos", {}) and (carpeta / nombre).exists():
        return
    espejo = tmp / (nombre + ".git")
    part = carpeta / (nombre + ".part")
    prueba = tmp / (nombre + ".prueba")
    shutil.rmtree(espejo, ignore_errors=True)
    try:
        if "://" not in origen:
            sup = subprocess.run(["git", "-C", origen, "rev-parse", "--is-shallow-repository"],
                                 capture_output=True, text=True)
            if sup.stdout.strip() == "true":
                raise ErrorArchivo(f"{origen} es un clon superficial (sin toda la historia): "
                                   f"correr antes  git -C {origen} fetch --unshallow")
        subprocess.run(["git", "clone", "--mirror", "--quiet", origen, str(espejo)], check=True)
        rev = subprocess.run(["git", "-C", str(espejo), "rev-parse", "HEAD"], check=True,
                             capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "-C", str(espejo), "bundle", "create", str(part), "--all"], check=True,
                       stderr=subprocess.DEVNULL)
        # que se pueda clonar sin nada más: es lo que va a hacer alguien dentro de unos años
        subprocess.run(["git", "clone", "--bare", "--quiet", str(part), str(prueba)], check=True,
                       stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError) as ex:
        part.unlink(missing_ok=True)
        raise ErrorArchivo(f"git falló: {ex}") from None
    finally:
        shutil.rmtree(espejo, ignore_errors=True)
        shutil.rmtree(prueba, ignore_errors=True)
    os.replace(part, carpeta / nombre)
    estado.update(rev=rev, fecha=ahora(),
                  archivos={nombre: {"tam": (carpeta / nombre).stat().st_size, "sha256": sha256_de(carpeta / nombre)}})
    guardar_estado(carpeta, e, estado)


def bajar_url(e, carpeta, estado):
    nombre = e.incluir[0] if e.incluir else Path(urllib.parse.urlparse(e.destino).path).name
    estado.setdefault("archivos", {})
    if nombre in estado["archivos"] and (carpeta / nombre).exists():
        return
    sha = transferir(abrir_http(e.destino), carpeta / nombre, None)
    estado.update(fecha=ahora())
    estado["archivos"][nombre] = {"tam": (carpeta / nombre).stat().st_size, "sha256": sha}
    guardar_estado(carpeta, e, estado)


# ---------------------------------------------------------------- comandos

def elegir(etapas, nums):
    if not nums:
        return etapas
    por_num = {s.num: s for s in etapas}
    faltan = [n for n in nums if n not in por_num]
    if faltan:
        raise ErrorArchivo(f"no hay etapa {faltan[0]} (hay de 1 a {len(etapas)})")
    return [por_num[n] for n in nums]


def cmd_etapas(etapas, _args):
    for s in etapas:
        print(f"\n[{s.num}] {s.titulo}")
        for e in s.entradas:
            marca = "  (VERIFICAR)" if e.estado == "verificar" else ""
            print(f"  - {e.fuente}{marca}\n      → {e.carpeta}\n      {e.nota}")
    print("\nTamaños reales: tamanos N · bajar: bajar N --destino DISCO")


def cmd_tamanos(etapas, args):
    destino = _destino(args, obligatorio=False)
    gran_total = 0
    for s in elegir(etapas, args.etapas):
        print(f"\n[{s.num}] {s.titulo}")
        total = 0
        for e in s.entradas:
            marca = " (VERIFICAR)" if e.estado == "verificar" else ""
            try:
                if e.tipo == "hf":
                    rev, archivos = plan_hf(e)
                elif e.tipo == "local":
                    archivos, rev = plan_local(e), None
                else:
                    print(f"  ?        {e.fuente}{marca}  (chico)")
                    continue
            except ErrorArchivo as ex:
                print(f"  ✗        {e.fuente}{marca}: {ex}")
                continue
            modelos = [a for a in archivos if e.tipo != "hf" or not coincide(a["ruta"], [], [], EXTRAS_HF)]
            tam = sum(a["tam"] for a in archivos)
            total += tam
            aviso = "  ← ningún archivo coincide" if not modelos else ""
            rv = f" @{rev[:8]}" if rev else ""
            print(f"  {_gb(tam):>9}  {e.fuente}{rv}{marca}  {len(archivos)} archivos{aviso}")
            if args.detalle:
                for a in archivos:
                    print(f"             {_gb(a['tam']):>9}  {a['ruta']}")
        gran_total += total
        print(f"  {_gb(total):>9}  total de la etapa")
    if len(elegir(etapas, args.etapas)) > 1:
        print(f"\n{_gb(gran_total)} en total")
    if destino:
        print(f"{_gb(shutil.disk_usage(destino).free)} libres en {destino}")


def cmd_buscar(_etapas, args):
    q = urllib.parse.urlencode({"search": args.texto, "sort": "downloads", "direction": "-1", "limit": args.n})
    data, _ = _json(f"{HF}/api/models?{q}")
    if not data:
        print("Nada.")
    for m in data:
        print(f"{m.get('downloads', 0):>9} desc. {m.get('likes', 0):>5} ♥  {m['id']}")


def _destino(args, obligatorio=True):
    d = args.destino or os.environ.get("ARCHIVO_DIR")
    if not d:
        if obligatorio:
            raise ErrorArchivo("falta --destino (o ARCHIVO_DIR): la carpeta del disco del archivo")
        return None
    d = Path(d).expanduser().resolve()
    if not d.is_dir():
        raise ErrorArchivo(f"{d} no existe: ¿está montado el disco?")
    if obligatorio and not args.mismo_disco and d.stat().st_dev == Path("/").stat().st_dev:
        raise ErrorArchivo(f"{d} está en el mismo disco que el sistema (¿el HDD no está montado?). "
                           "Si es a propósito: --mismo-disco")
    return d


def _herramientas(destino):
    h = destino / "_herramientas"
    h.mkdir(exist_ok=True)
    for f in (Path(__file__).resolve(), MANIFIESTO, AQUI / "LEEME.md"):
        if f.exists():
            shutil.copy2(f, h / f.name)


def escribir_indice(destino):
    filas = []
    for est in sorted(destino.rglob(".archivo.json")):
        if "_herramientas" in est.parts:
            continue
        d = leer_estado(est.parent)
        tam = sum(a["tam"] for a in d.get("archivos", {}).values())
        filas.append(f"| `{est.parent.relative_to(destino)}` | {_gb(tam)} | {d.get('fecha', '?')} | {d.get('nota', '')} |\n")
    libre = shutil.disk_usage(destino).free
    (destino / "INDICE.md").write_text(
        "# Archivo de modelos\n\nCada carpeta tiene ARCHIVO.md (origen y revisión) y SHA256SUMS "
        "(`sha256sum -c SHA256SUMS`). El script y el manifiesto con que se armó están en `_herramientas/`.\n\n"
        f"Actualizado: {ahora()} · libre: {_gb(libre)}\n\n| Carpeta | Tamaño | Archivado | Nota |\n|---|---|---|---|\n"
        + "".join(filas))


def cmd_bajar(etapas, args):
    destino = _destino(args)
    _herramientas(destino)
    tmp = destino / ".tmp"
    tmp.mkdir(exist_ok=True)
    enlaces, fallas = {}, []
    try:
        for s in elegir(etapas, args.etapas):
            print(f"\n[{s.num}] {s.titulo}")
            for e in s.entradas:
                if args.solo and args.solo not in e.fuente and args.solo not in e.carpeta:
                    continue
                if e.estado == "verificar" and not args.incluir_sin_verificar:
                    print(f"  salteado (VERIFICAR en el manifiesto): {e.fuente}")
                    continue
                print(f"  {e.fuente} → {e.carpeta}")
                carpeta = destino / e.carpeta
                carpeta.mkdir(parents=True, exist_ok=True)
                estado = leer_estado(carpeta)
                try:
                    if e.tipo == "hf":
                        bajar_hf(e, carpeta, estado)
                    elif e.tipo == "local":
                        bajar_local(e, carpeta, estado, enlaces)
                    elif e.tipo == "git":
                        bajar_git(e, carpeta, estado, tmp)
                    else:
                        bajar_url(e, carpeta, estado)
                    print("    listo")
                except ErrorArchivo as ex:
                    print(f"    ✗ {ex}")
                    fallas.append(e.fuente)
                finally:
                    if not any(carpeta.iterdir()):
                        carpeta.rmdir()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        escribir_indice(destino)
    if fallas:
        print(f"\nCon problemas ({len(fallas)}): " + ", ".join(fallas))
        return 1
    print("\nTodo listo. INDICE.md actualizado.")
    return 0


def cmd_verificar(_etapas, args):
    destino = _destino(args)
    mal = 0
    for est in sorted(destino.rglob(".archivo.json")):
        if "_herramientas" in est.parts:
            continue
        carpeta = est.parent
        for ruta, a in sorted(leer_estado(carpeta).get("archivos", {}).items()):
            p = carpeta / ruta
            nombre = f"{carpeta.relative_to(destino)}/{ruta}"
            if not p.exists():
                print(f"  FALTA  {nombre}")
                mal += 1
                continue
            ok = sha256_de(p) == a["sha256"]
            mal += not ok
            print(f"  {'ok   ' if ok else 'MAL  '}  {nombre}")
    print("\nTodo bien." if not mal else f"\n{mal} con problemas: volver a bajarlos (borrar el archivo y correr bajar).")
    return 1 if mal else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("etapas", help="lista las etapas (es lo que hace sin argumentos)")
    t = sub.add_parser("tamanos", help="tamaño real de cada entrada y si el repo existe")
    t.add_argument("etapas", nargs="*", type=int)
    t.add_argument("--detalle", action="store_true", help="lista cada archivo")
    t.add_argument("--destino")
    b = sub.add_parser("buscar", help="busca repos en Hugging Face, ordenados por descargas")
    b.add_argument("texto")
    b.add_argument("-n", type=int, default=15)
    d = sub.add_parser("bajar", help="copia o baja las etapas pedidas al disco del archivo")
    d.add_argument("etapas", nargs="+", type=int)
    d.add_argument("--destino")
    d.add_argument("--solo", help="solo las entradas cuya fuente o carpeta contenga este texto")
    d.add_argument("--incluir-sin-verificar", action="store_true", help="bajar también las marcadas verificar")
    d.add_argument("--mismo-disco", action="store_true", help="permitir un destino en el disco del sistema")
    v = sub.add_parser("verificar", help="vuelve a calcular el sha256 de todo lo archivado")
    v.add_argument("--destino")
    v.add_argument("--mismo-disco", action="store_true")
    args = ap.parse_args(argv)
    try:
        etapas = leer_manifiesto(MANIFIESTO.read_text())
        comando = {"tamanos": cmd_tamanos, "buscar": cmd_buscar, "bajar": cmd_bajar,
                   "verificar": cmd_verificar}.get(args.cmd, cmd_etapas)
        return comando(etapas, args) or 0
    except ErrorArchivo as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCortado. Lo bajado queda guardado: el mismo comando sigue desde ahí.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
