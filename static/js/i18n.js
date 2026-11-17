// Idioma de la interfaz (castellano o ingles). El texto en castellano es la clave: t('Cargar') -> 'Load'.
// Sin traduccion queda en castellano. El idioma se elige con el selector de arriba y se recuerda en el navegador;
// la primera vez sale del idioma del navegador. El servidor recibe el mismo idioma en X-Lang (llama_panel/i18n.py).

const KEY = 'llama-panel-lang';
const norm = s => String(s).replace(/\s+/g, ' ').trim();

function initial() {
  try { const v = localStorage.getItem(KEY); if (v === 'es' || v === 'en') return v; } catch { /* sin storage */ }
  return /^es\b/i.test(navigator.language || '') ? 'es' : 'en';
}
let LANG = initial();
document.documentElement.lang = LANG;

export const lang = () => LANG;
export const locale = () => (LANG === 'es' ? 'es-AR' : 'en-US');

export function setLang(v) {
  try { localStorage.setItem(KEY, v); } catch { /* sin storage: vale hasta recargar */ }
  location.reload();
}

export function t(text, vars) {
  let s = LANG === 'en' ? (EN[text] ?? EN[norm(text)] ?? text) : text;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));
  return s;
}

// Textos fijos de index.html: nodos de texto, atributos y los elementos con data-i18n-html (llevan <code>, <a>).
export function translateDom(root) {
  if (LANG === 'es') return;
  for (const node of root.querySelectorAll('[data-i18n-html]')) {
    const tr = EN[norm(node.innerHTML)];
    if (tr) node.innerHTML = tr;
  }
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (n.parentElement.closest('[data-i18n-html], script, style, code, pre')) continue;
    const key = norm(n.nodeValue);
    if (key && EN[key]) n.nodeValue = n.nodeValue.replace(n.nodeValue.trim(), EN[key]);
  }
  for (const node of root.querySelectorAll('[title], [placeholder], [data-confirm], [aria-label]')) {
    for (const a of ['title', 'placeholder', 'data-confirm', 'aria-label']) {
      const v = node.getAttribute(a);
      if (v && EN[norm(v)]) node.setAttribute(a, EN[norm(v)]);
    }
  }
}

export const EN = {
  // index.html
  'Panel IA': 'AI Panel',
  'Sin conexión con el panel': 'No connection to the panel',
  'Modo IA': 'AI mode',
  'Salir del modo IA': 'Leave AI mode',
  '¿Cerrar la sesión gráfica de la máquina del panel? Tocá de nuevo': "Close the graphical session on the panel's machine? Tap again",
  'Cierra la sesión gráfica de la máquina del panel (se pierde lo abierto sin guardar) para dejarle toda la VRAM a los modelos, y muestra un dashboard en su pantalla. Pensado para un servidor dedicado':
    "Closes the graphical session on the panel's machine (unsaved work is lost) to leave all the VRAM to the models, and shows a dashboard on its screen. Meant for a dedicated server",
  'Vuelve a prender el escritorio': 'Turns the desktop back on',
  'No instalado: correr una vez sudo ~/llama-panel/scripts/install-modo-ia.sh':
    'Not installed: run once  sudo ~/llama-panel/scripts/install-modo-ia.sh',
  'Falta acceso permanente a la GPU: correr de nuevo el instalador del modo IA y reiniciar':
    'Missing permanent GPU access: run the AI mode installer again and reboot',
  'Apagar todo': 'Stop all',
  '¿Seguro? Tocá de nuevo': 'Sure? Tap again',
  'Apaga texto e imágenes y libera la VRAM': 'Stops text and images and frees the VRAM',
  'Uso de GPU': 'GPU usage',
  'Temperatura': 'Temperature',
  'Texto': 'Text',
  'Imágenes': 'Images',
  'Modelo cargado': 'Loaded model',
  'Última respuesta': 'Last response',
  'Modelo': 'Model',
  'Actividad': 'Activity',
  'Encender': 'Start',
  'Reiniciar': 'Restart',
  'Apagar': 'Stop',
  'Abrir chat ↗': 'Open chat ↗',
  'Abrir StableUI ↗': 'Open StableUI ↗',
  'Texto e imágenes comparten la VRAM: al encender uno, el otro se apaga solo.':
    'Text and images share the VRAM: starting one stops the other.',
  'Modelos de texto': 'Text models',
  'Modelos de imagen': 'Image models',
  'Rendimiento': 'Performance',
  'Registros': 'Logs',
  'Configuración': 'Setup',
  'Buscar modelos nuevos': 'Scan for new models',
  'Vuelve a leer la carpeta de modelos y se lo avisa al servidor': 'Re-reads the model folder and tells the server',
  'Ajustes globales': 'Global settings',
  'valen para todos los modelos': 'apply to every model',
  'Una opción por línea: <code>clave = valor</code>, con el nombre largo de la opción de llama-server sin los guiones (ej. <code>ctx-size = 8192</code>).':
    'One option per line: <code>key = value</code>, using the long llama-server option name without the dashes (e.g. <code>ctx-size = 8192</code>).',
  'Todavía no hay modelos o falta llama-server. <a href="#setup" data-goto="setup">Configurá las carpetas y programas</a>.':
    'No models yet or llama-server is missing. <a href="#setup" data-goto="setup">Set up the folders and programs</a>.',
  'Las opciones extra son flags de KoboldCpp separados por espacios (ej. <code>--sdoffloadcpu</code>, <code>--sdtiledvae 768</code>, <code>--sdlora /ruta/archivo.safetensors</code>).':
    'Extra options are KoboldCpp flags separated by spaces (e.g. <code>--sdoffloadcpu</code>, <code>--sdtiledvae 768</code>, <code>--sdlora /path/file.safetensors</code>).',
  'Texto: velocidad por respuesta': 'Text: speed per response',
  'Generación (tokens/s) · últimas respuestas': 'Generation (tokens/s) · latest responses',
  'Por modelo': 'By model',
  'Respuestas recientes': 'Recent responses',
  'Imágenes: generaciones recientes': 'Images: recent generations',
  'Filtrar…': 'Filter…',
  'En vivo': 'Live',
  'Cambios sin guardar': 'Unsaved changes',
  'Descartar': 'Discard',
  'Guardar': 'Save',
  'Guardar y aplicar': 'Save and apply',
  // estados
  'Encendido': 'Running',
  'Arrancando': 'Starting',
  'Recargando': 'Reloading',
  'Apagando': 'Stopping',
  'Apagado': 'Stopped',
  'Cargado': 'Loaded',
  'Cargando': 'Loading',
  'En reposo': 'Sleeping',
  'Descargando': 'Downloading',
  'Falló': 'Failed',
  'Sin cargar': 'Not loaded',
  // motores
  '(cargando…)': '(loading…)',
  'ninguno (se carga al pedirlo)': 'none (loads on request)',
  'ninguno': 'none',
  'Generando · paso {step}/{steps}': 'Generating · step {step}/{steps}',
  'faltan {time}': '{time} left',
  'En cola: {n}': 'Queued: {n}',
  'Libre · {n} imágenes generadas': 'Idle · {n} images generated',
  'Cargando el modelo…': 'Loading the model…',
  // modelos de texto
  'razonamiento: {value}': 'reasoning: {value}',
  'ctx global': 'global ctx',
  '+{n} opciones': '+{n} options',
  '+1 opción': '+1 option',
  'Se aplican solo con el modo IA activo': 'Only applied while AI mode is on',
  '+{n} en modo IA': '+{n} in AI mode',
  'No entra completo en la VRAM: parte va a la RAM y será más lento': "Doesn't fully fit in VRAM: part goes to RAM and it will be slower",
  'más grande que la VRAM': 'larger than VRAM',
  'llama-server no conoce esta opción (¿mal escrita?)': "llama-server doesn't know this option (typo?)",
  'repetida: vale la última': 'repeated: the last one wins',
  'en modo IA reemplaza a la de arriba': 'in AI mode it replaces the one above',
  'opción válida': 'valid option',
  'pisa': 'overrides',
  'Mediana de las últimas {n} respuestas: generación {g} tok/s': 'Median of the last {n} responses: generation {g} tok/s',
  ', prompt {p} tok/s (prompts de 512 tokens o más)': ', prompt {p} tok/s (prompts of 512 tokens or more)',
  ', MTP acierta {acc} %': ', MTP accepts {acc} %',
  'No hay archivos .gguf en {dir}.': 'No .gguf files in {dir}.',
  'Guarda a disco el prompt que el modelo ya procesó (por ejemplo, las instrucciones y herramientas de un agente). Se restaura solo cada vez que el modelo se carga.':
    "Saves to disk the prompt the model already processed (for example, an agent's instructions and tools). It is restored automatically every time the model loads.",
  'Guardar prompt': 'Save prompt',
  'Borrar prompt': 'Delete prompt',
  'Prompt guardado · {size}': 'Saved prompt · {size}',
  'Guardado el {date} ({size}). Clic en "Prompt guardado" lo reemplaza por lo que el modelo tiene procesado ahora.':
    'Saved on {date} ({size}). Clicking "Saved prompt" replaces it with what the model has processed now.',
  'Cargar': 'Load',
  'Cargando…': 'Loading…',
  'Descargar': 'Unload',
  'Ajustes': 'Settings',
  'MTP (borradores por paso)': 'MTP (drafts per step)',
  'Razonamiento': 'Reasoning',
  'Contexto': 'Context',
  'Opciones extra de llama-server · una por línea: <code>clave = valor</code> · <code>#</code> para comentarios':
    'Extra llama-server options · one per line: <code>key = value</code> · <code>#</code> for comments',
  'Opciones extra en modo IA · se suman a las de arriba y las pisan, solo con el escritorio apagado (~0.4 GB más de VRAM)':
    'Extra options in AI mode · added to the ones above and override them, only with the desktop off (~0.4 GB more VRAM)',
  'ej: ctx-size = 32768': 'e.g. ctx-size = 32768',
  // modelos de imagen
  'No hay modelos (.gguf / .safetensors de más de 100 MB) en {dir}.': 'No models (.gguf / .safetensors over 100 MB) in {dir}.',
  'solo el modelo': 'model only',
  'Seleccionado': 'Selected',
  'Opciones extra': 'Extra options',
  'ej: --sdoffloadcpu --sdtiledvae 768': 'e.g. --sdoffloadcpu --sdtiledvae 768',
  'Usar este': 'Use this one',
  'Reiniciar con este': 'Restart with this one',
  'Cambiar a este': 'Switch to this one',
  'Usar y encender': 'Use and start',
  // rendimiento
  'Generación (mediana)': 'Generation (median)',
  'Prompt largo (mediana)': 'Long prompt (median)',
  'Aceptación MTP': 'MTP acceptance',
  'Respuestas registradas': 'Logged responses',
  'Todavía no hay respuestas registradas. Generá algo en el chat.': 'No responses logged yet. Generate something in the chat.',
  'Hora': 'Time',
  'Tokens prompt': 'Prompt tokens',
  'Prompt tok/s': 'Prompt tok/s',
  'Tokens generados': 'Generated tokens',
  'Generación tok/s': 'Generation tok/s',
  'Todavía no hay imágenes registradas.': 'No images logged yet.',
  'Pasos': 'Steps',
  'Duración': 'Duration',
  'Segundos por paso': 'Seconds per step',
  'Sin datos todavía.': 'No data yet.',
  'Respuestas': 'Responses',
  'Mejor': 'Best',
  'Mediana con prompts de 512 tokens o más: con prompts cortos la velocidad no dice nada':
    'Median over prompts of 512 tokens or more: with short prompts the speed means nothing',
  'Prompt largo': 'Long prompt',
  'Aciertos MTP': 'MTP hits',
  'Última': 'Last',
  '· atenuadas: otro modelo que {model}': '· dimmed: a model other than {model}',
  'Tokens por segundo de generación en las últimas respuestas': 'Generation tokens per second in the latest responses',
  'generando': 'generating',
  '{pct}% aceptado': '{pct}% accepted',
  // registros
  '(vacío)': '(empty)',
  // configuración
  '/ruta/a/modelos': '/path/to/models',
  'Quitar esta carpeta': 'Remove this folder',
  'Quitar': 'Remove',
  'Los campos grises están fijados por una variable de entorno del servicio y no se pueden cambiar desde acá.':
    "Greyed-out fields are set by an environment variable of the service and can't be changed here.",
  'Carpetas donde buscar archivos <code>.gguf</code> (también en subcarpetas). Conviene que los modelos grandes estén en un disco rápido.':
    'Folders to search for <code>.gguf</code> files (subfolders included). Large models are best kept on a fast disk.',
  'Agregar carpeta': 'Add folder',
  'Programas': 'Programs',
  'De llama.cpp, compilado con el backend de tu GPU (Vulkan, CUDA, ROCm…). Ej.: <code>~/llama.cpp/build/bin/llama-server</code>':
    'From llama.cpp, built with your GPU backend (Vulkan, CUDA, ROCm…). E.g. <code>~/llama.cpp/build/bin/llama-server</code>',
  'Opcional, para generar imágenes.': 'Optional, for image generation.',
  'Imágenes y GPU': 'Images and GPU',
  'Carpeta de modelos de imagen': 'Image model folder',
  'Opcional. <code>.safetensors</code> o <code>.gguf</code> de Stable Diffusion / Flux.':
    'Optional. Stable Diffusion / Flux <code>.safetensors</code> or <code>.gguf</code>.',
  'Nombre de la GPU': 'GPU name',
  'Opcional, solo para mostrar. Detectado: <b>{name}</b>.': 'Optional, display only. Detected: <b>{name}</b>.',
  'Rutas aplicadas.': 'Paths applied.',
  // varios
  'carga {load}': 'load {load}',
  'ventilador {pct} %': 'fan {pct} %',
  'hace {time}': '{time} ago',
  'No se pudo contactar al panel.': 'Could not reach the panel.',
};
