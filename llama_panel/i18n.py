"""Idioma de los mensajes que el panel le devuelve a la interfaz (castellano o inglés).

El texto en castellano es la clave: `_("{n} modelos encontrados.", n=3)`. La interfaz manda su idioma en el
encabezado X-Lang de cada pedido y server.py lo fija para ese pedido. Sin traducción, queda en castellano.
"""
import contextvars

LANGS = ("es", "en")
LANG = contextvars.ContextVar("lang", default="es")


def set_lang(value):
    LANG.set(value if value in LANGS else "es")


_ui_lang = {"v": None}


def remember_ui_lang(value):
    """Guarda el idioma que usa la interfaz web (ui-lang): el dashboard del modo IA lo sigue."""
    if value not in LANGS or value == _ui_lang["v"]:
        return
    from . import config as C
    f = C.CFG_DIR / "ui-lang"
    try:
        if not f.exists() or f.read_text().strip() != value:
            C.CFG_DIR.mkdir(parents=True, exist_ok=True)
            f.write_text(value + "\n")
        _ui_lang["v"] = value
    except OSError:
        pass


def _(text, **kw):
    if LANG.get() == "en":
        text = EN.get(text, text)
    return text.format(**kw) if kw else text


EN = {
    # acciones
    "Servidor de texto apagado.": "Text server stopped.",
    "Servidor de texto reiniciado ({n} modelos).": "Text server restarted ({n} models).",
    "Servidor de texto encendido ({n} modelos).": "Text server started ({n} models).",
    "No hay modelos de imagen en {dir}.": "No image models in {dir}.",
    "Generador de imágenes reiniciado con {model}.": "Image generator restarted with {model}.",
    "Generador de imágenes encendido con {model}.": "Image generator started with {model}.",
    "Generador de imágenes apagado.": "Image generator stopped.",
    "No se pudo activar el modo IA.": "Could not turn on AI mode.",
    "No se pudo desactivar el modo IA.": "Could not turn off AI mode.",
    "Modo IA activado: escritorio apagado, dashboard en la pantalla.":
        "AI mode on: desktop stopped, dashboard on the screen.",
    "Modo IA desactivado: el escritorio vuelve en unos segundos.":
        "AI mode off: the desktop comes back in a few seconds.",
    "Todo apagado: la VRAM queda libre.": "Everything stopped: VRAM is free.",
    "Acción desconocida.": "Unknown action.",
    " Servidor de texto encendido.": " Text server started.",
    "El servidor de texto no responde. Mirá la pestaña Registros.":
        "The text server is not responding. Check the Logs tab.",
    "No existe el modelo '{model}'.": "Model '{model}' does not exist.",
    "No se pudo cargar {model}: {err}": "Could not load {model}: {err}",
    " Se descargó {models}.": " Unloaded {models}.",
    "Cargando {model}…": "Loading {model}…",
    "No se pudo descargar {model}: {err}": "Could not unload {model}: {err}",
    "{model} descargado: la VRAM queda libre.": "{model} unloaded: VRAM is free.",
    "El modelo tiene que estar cargado.": "The model has to be loaded.",
    "No se pudo guardar el prompt: {err}": "Could not save the prompt: {err}",
    "Prompt «{name}» guardado: {tokens} tokens. Se precarga solo cada vez que {model} se cargue.":
        "Prompt «{name}» saved: {tokens} tokens. It is preloaded every time {model} loads.",
    "Prompt guardado: {tokens} tokens. Se restaura solo cada vez que {model} se cargue.":
        "Prompt saved: {tokens} tokens. It is restored automatically every time {model} loads.",
    "No se pudo restaurar el prompt: {err}": "Could not restore the prompt: {err}",
    "Prompt restaurado: {tokens} tokens.": "Prompt restored: {tokens} tokens.",
    "No hay un prompt guardado para la configuración actual.": "There is no saved prompt for the current settings.",
    "Prompt guardado borrado.": "Saved prompt deleted.",
    "No había un prompt guardado.": "There was no saved prompt.",
    "El servidor estaba apagado: se aplica cuando lo enciendas.": "The server was off: it applies when you start it.",
    "Servidor reiniciado con la nueva configuración.": "Server restarted with the new settings.",
    "Aplicado: {models} se está recargando con los ajustes nuevos.": "Applied: {models} is reloading with the new settings.",
    "Aplicado: el servidor ya usa la nueva configuración.": "Applied: the server already uses the new settings.",
    "{n} modelos encontrados, pero el servidor no pudo recargar la lista: {err}":
        "{n} models found, but the server could not reload the list: {err}",
    "{n} modelos encontrados. El servidor ya los conoce.": "{n} models found. The server already knows them.",
    "{n} modelos encontrados.": "{n} models found.",
    "Formato inválido.": "Invalid format.",
    "No se guardó. Revisá estos campos (las opciones van como clave = valor):":
        "Not saved. Check these fields (options go as key = value):",
    "Guardado ({n} modelos).": "Saved ({n} models).",
    "Se aplica en la próxima carga.": "It applies on the next load.",
    "modelo '{model}' no existe en {dir}": "model '{model}' does not exist in {dir}",
    "No se guardó.": "Not saved.",
    "ninguno": "none",
    "Guardado (modelo de imagen: {model}) y generador reiniciado.": "Saved (image model: {model}) and generator restarted.",
    "Guardado (modelo de imagen: {model}). El generador está apagado: se aplica al encenderlo.":
        "Saved (image model: {model}). The generator is off: it applies when you start it.",
    "Guardado (modelo de imagen: {model}). Se aplica en el próximo arranque del generador.":
        "Saved (image model: {model}). It applies the next time the generator starts.",
    # services
    "El modo IA no está instalado: correr una vez  sudo ~/llama-panel/scripts/install-modo-ia.sh":
        "AI mode is not installed: run once  sudo ~/llama-panel/scripts/install-modo-ia.sh",
    "Sin escritorio, los modelos perderían el acceso a la GPU. Correr de nuevo  "
    "sudo ~/llama-panel/scripts/install-modo-ia.sh  (agrega los grupos render y video) y reiniciar.":
        "Without a desktop, the models would lose GPU access. Run again  "
        "sudo ~/llama-panel/scripts/install-modo-ia.sh  (it adds the render and video groups) and reboot.",
    " Generador de imágenes apagado para liberar VRAM.": " Image generator stopped to free VRAM.",
    " Servidor de texto apagado para liberar VRAM.": " Text server stopped to free VRAM.",
    # validación de ajustes
    "{key}: opciones extra mal escritas ({err})": "{key}: malformed extra options ({err})",
    "{key}: '{token}' tiene caracteres no permitidos (solo letras, números y . _ / : = + , -)":
        "{key}: '{token}' has characters that are not allowed (only letters, digits and . _ / : = + , -)",
    "{key}: borradores MTP debe ser un número entre 1 y 16 (puso '{value}')":
        "{key}: MTP drafts must be a number from 1 to 16 (got '{value}')",
    "{key}: contexto debe ser un número de 256 o más (puso '{value}')":
        "{key}: context must be a number of 256 or more (got '{value}')",
    "{key}: razonamiento inválido '{value}'": "{key}: invalid reasoning '{value}'",
    # prompt guardado
    "el modelo está respondiendo; esperar a que termine": "the model is answering; wait until it finishes",
    "el slot está vacío: mandar primero un mensaje desde el cliente": "the slot is empty: send a message from the client first",
    # configuración (paths.py)
    "Datos inválidos.": "Invalid data.",
    "vacío": "empty",
    "no existe": "does not exist",
    "no es una carpeta": "not a folder",
    "no es un ejecutable": "not an executable",
    "encontrado": "found",
    "{n} {what}": "{n} {what}",
    "{n} o más {what} (carpeta muy grande, no se revisó toda)": "{n} or more {what} (very large folder, not fully scanned)",
    "sin {what} todavía": "no {what} yet",
    "modelos .gguf": ".gguf models",
    "archivos de modelo de imagen": "image model files",
    "sin carpeta (opcional)": "no folder (optional)",
    "opcional: solo para imágenes": "optional: only for images",
    "no existe (opcional: solo para imágenes)": "does not exist (optional: only for images)",
    "llama.cpp {version}": "llama.cpp {version}",
    "encontrado (no respondió a --version)": "found (did not answer --version)",
    "Hace falta al menos una carpeta de modelos de texto.": "At least one text model folder is needed.",
    "La ruta «{path}» tiene saltos de línea.": "The path «{path}» has line breaks.",
    "«{path}» tiene que ser una ruta absoluta (empezar con / o ~).": "«{path}» has to be an absolute path (start with / or ~).",
    "La ruta de {prog} no puede tener espacios (va en un servicio de systemd).":
        "The {prog} path cannot have spaces (it goes in a systemd service).",
    "Las carpetas de modelos no pueden tener «:».": "Model folders cannot contain «:».",
    "Falta la ruta de llama-server.": "The llama-server path is missing.",
    "Guardado. El panel se reinicia para usar las rutas nuevas.": "Saved. The panel restarts to use the new paths.",
    " El servidor de texto usa el llama-server nuevo la próxima vez que se encienda o reinicie.":
        " The text server uses the new llama-server the next time it starts or restarts.",
    "{key}: valor inválido en «{field}»: '{value}'": "{key}: invalid value in «{field}»: '{value}'",
    # nombres de los campos de cada modelo (text_models.FIELDS), para los mensajes de error
    "Contexto": "Context",
    "Capas en GPU": "GPU layers",
    "Expertos MoE en RAM": "MoE experts in RAM",
    "Pedidos en paralelo": "Parallel requests",
    "Caché KV": "KV cache",
    "Flash attention": "Flash attention",
    "Tanda del prompt": "Prompt batch",
    "Hilos de CPU": "CPU threads",
    "MTP": "MTP",
    "Borradores por paso": "Drafts per step",
    "Razonamiento": "Reasoning",
    # instalación (install.py)
    "Configuración de texto generada con {n} modelos: {file}": "Text configuration generated with {n} models: {file}",
    "Generador de imágenes configurado con el modelo: {model}": "Image generator set up with the model: {model}",
    "(No hay modelos de imagen todavía en {dir}.)": "(No image models yet in {dir}.)",
    "AVISO: no existe {path}. Elegí llama-server en la pestaña Configuración del panel.":
        "WARNING: {path} does not exist. Choose llama-server in the panel's Setup tab.",
    "(KoboldCpp no está en {path}: es opcional, solo para imágenes.)": "(KoboldCpp is not at {path}: optional, only for images.)",
    "panel habilitado": "panel enabled",
    "auto-reinicio habilitado": "auto-restart enabled",
    "auto-reinicio activo": "auto-restart active",
    "panel (re)iniciado": "panel (re)started",
    "Servidor de modelos de texto reiniciado con la nueva configuración.": "Text model server restarted with the new settings.",
    "Arranque con la máquina: activo (linger).": "Start at boot: on (linger).",
    "Arranque con la máquina: activado (linger).": "Start at boot: turned on (linger).",
    "AVISO: el panel no va a arrancar solo al prender la máquina hasta que inicies sesión. Para que arranque"
    " siempre:  sudo loginctl enable-linger {user}":
        "WARNING: the panel won't start at boot until you log in. To always start it:  sudo loginctl enable-linger {user}",
    "\nListo. Abrí http://{host}.local:{port} desde otra computadora de la red (o http://localhost:{port} en esta).":
        "\nDone. Open http://{host}.local:{port} from another computer on the network (or http://localhost:{port} on this one).",
    # dashboard del modo IA (kiosk.py); los rótulos de columna se rellenan después de traducir
    "hace {t}": "{t} ago",
    "libre": "free",
    "pico": "peak",
    "núcleo {mhz} MHz · {mem} · ventilador {fan}": "core {mhz} MHz · {mem} · fan {fan}",
    "memoria ?": "memory ?",
    "fija": "pinned",
    "memoria {mhz} MHz": "memory {mhz} MHz",
    "TEMP {t} °C · núcleo más caliente {p} °C": "TEMP {t} °C · hottest core {p} °C",
    "TEMP sensor no disponible": "TEMP sensor not available",
    "carga {load} · {n} hilos": "load {load} · {n} threads",
    "caché": "cache",
    "memoria · RAM": "memory · RAM",
    "núcl.": "cores",
    "prueba fuera del panel": "test outside the panel",
    "texto": "text",
    "imágenes": "images",
    "ninguno cargado": "none loaded",
    "paso {a}/{b}": "step {a}/{b}",
    "última: {g} tok/s · prompt {p} · {ago}": "last: {g} tok/s · prompt {p} · {ago}",
    "tok/s de las últimas {n} respuestas": "tok/s of the last {n} responses",
    "modelos": "models",
    "Encendido": "Running",
    "Arrancando": "Starting",
    "Apagando": "Stopping",
    "Apagado": "Stopped",
    "generando": "generating",
    "procesando el prompt": "processing the prompt",
    "esperando": "waiting",
    "prompt": "prompt",
    "media": "avg",
    "(pedido anterior)": "(previous request)",
    "salida": "output",
    "contexto": "context",
    "tokens": "tokens",
    "reuso": "reused",
    "prompt total": "total prompt",
    "tiempo": "time",
    "estimado prompt restante ~{t}": "estimated prompt left ~{t}",
    "tok/paso": "tok/step",
    "-- (se informa al finalizar)": "-- (reported at the end)",
    "MTP últ.": "MTP last",
    "historial {n} resp. · {t} tok · {r} tok/s": "history {n} resp. · {t} tok · {r} tok/s",
    "último dato hace {t}": "last data {t} ago",
    "sin datos del pedido": "no data for the request",
    "en vivo": "live",
    "proceso": "process",
    "hilos": "threads",
    "(sin modelos corriendo)": "(no models running)",
    "procesos": "processes",
    "carga": "load",
    "cpu · memoria": "cpu · memory",
    "PANEL IA · modo IA": "AI PANEL · AI mode",
    "Sin conexión con el panel. Reintentando…": "No connection to the panel. Retrying…",
    "No se pudo salir del modo IA:": "Could not leave AI mode:",
    "Volviendo al escritorio… (puede tardar unos segundos)": "Going back to the desktop… (it may take a few seconds)",
    "¿Volver al escritorio? Apretá S de nuevo en {secs} s": "Back to the desktop? Press S again within {secs} s",
    " Para volver al escritorio: S dos veces · «Salir del modo IA» en el panel · Ctrl+Alt+F2 y  sudo llama-modo-ia off":
        " Back to the desktop: S twice · «Leave AI mode» in the panel · Ctrl+Alt+F2 and  sudo llama-modo-ia off",
    " Volver al escritorio: S dos veces · Ctrl+Alt+F2 y  sudo llama-modo-ia off":
        " Back to the desktop: S twice · Ctrl+Alt+F2 and  sudo llama-modo-ia off",
    # opciones de razonamiento (config.REASONING_OPTS)
    # encendido a demanda (gate.py)
    "El generador de imágenes no responde. Mirá la pestaña Registros.":
        "The image generator is not responding. Check the Logs tab.",
    "El generador de imágenes no arrancó. Mirá la pestaña Registros.":
        "The image generator did not start. Check the Logs tab.",
    "El generador de imágenes está ocupado; probá de nuevo en un rato.": "The image generator is busy; try again later.",
    "El servidor de texto está respondiendo; probá de nuevo en un rato.": "The text server is busy; try again later.",
    "El servicio no respondió.": "The service did not respond.",
    "El servidor de texto está apagado: se prende con el primer pedido.":
        "The text server is off: it starts with the first request.",
    "El generador de imágenes está apagado: se prende con el primer pedido.":
        "The image generator is off: it starts with the first request.",
    "No se pudo usar el puerto {port} ({err}). Reiniciá el servicio que lo ocupa.":
        "Could not use port {port} ({err}). Restart the service that holds it.",
    "Pedido de texto: servidor de texto encendido en {s} s.": "Text request: text server started in {s} s.",
    "Pedido de imagen: generador encendido con {model} en {s} s.":
        "Image request: generator started with {model} in {s} s.",
    "{min} min sin uso: {models} descargado.": "{min} min idle: {models} unloaded.",
    "{min} min sin uso: generador de imágenes apagado.": "{min} min idle: image generator stopped.",
    "Los minutos sin uso van de 0 (nunca) a 1440.": "Idle minutes go from 0 (never) to 1440.",
    "Descarga por inactividad: después de {n} min sin uso.": "Idle unload: after {n} min without use.",
    "Descarga por inactividad: apagada.": "Idle unload: off.",
    "Guardado.": "Saved.",
    " Se reiniciaron los servicios encendidos para cambiarlos de puerto.":
        " The running services were restarted to move them to the new port.",
    "Guardado, pero el panel no pudo atender los puertos {ports}: {err}":
        "Saved, but the panel could not take ports {ports}: {err}",
    "Encendido a demanda activo: el panel atiende los puertos {ports} y prende lo que pida cada cliente.":
        "On-demand start on: the panel serves ports {ports} and starts whatever each client asks for.",
    "Encendido a demanda apagado: los servicios vuelven a atender los puertos {ports}.":
        "On-demand start off: the services serve ports {ports} again.",
    # compañero (buddy.py)
    "Charla nueva.": "New chat.",
    "Compañero guardado.": "Buddy saved.",
    "Demasiadas herramientas seguidas; corté acá.": "Too many tool calls in a row; stopped here.",
    "El compañero está contestando otra cosa; esperá un momento.": "The buddy is answering something else; wait a moment.",
    "El compañero necesita un nombre.": "The buddy needs a name.",
    "Herramienta desconocida.": "Unknown tool.",
    "Memoria guardada.": "Memory saved.",
    "Mensaje vacío.": "Empty message.",
    "No hay un modelo de texto cargado: cargá uno o elegí cuál usar en los ajustes del compañero.":
        "No text model is loaded: load one or pick which one to use in the buddy settings.",
    "No se entendió nada; probá de nuevo.": "Nothing was understood; try again.",
    "Sin voz: {err}": "No voice: {err}",
    "Token de Home Assistant borrado.": "Home Assistant token deleted.",
    "Home Assistant conectado: {msg}": "Home Assistant connected: {msg}",
    "Token guardado, pero Home Assistant no responde: {msg}": "Token saved, but Home Assistant does not respond: {msg}",
    "por defecto": "default",
    "auto": "auto",
    "activado": "on",
    "apagado": "off",
}
