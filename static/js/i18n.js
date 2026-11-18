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
  '(no se entendió)': '(not understood)',
  'La clave se envía al backend y queda en un archivo privado; nunca se vuelve a mostrar en el navegador. Si dejás el campo vacío y guardás, se borra y se usa DuckDuckGo experimental.':
    'The key is sent to the backend and stored in a private file; it is never shown in the browser again. Save an empty field to delete it and use experimental DuckDuckGo.',
  'Guardar clave': 'Save key',
  'configurada': 'configured',
  'sin configurar': 'not configured',
  'Desactivado': 'Disabled',
  'Espacio': 'Space',
  'Botón lateral {n}': 'Side mouse button {n}',
  'Conversación activa': 'Active conversation',
  'Eliminar conversación activa': 'Delete active conversation',
  'Enviado desde el cliente de PC': 'Sent from the PC client',
  '¿Eliminar?': 'Delete?',
  'Mantené apretado para hablar': 'Hold to talk',
  'Mantené apretado para hablar · Atajo: {key}': 'Hold to talk · Shortcut: {key}',
  'Atajo para hablar en este navegador': 'Push-to-talk shortcut in this browser',
  'Definir atajo': 'Set shortcut',
  'Borrar': 'Clear',
  'Sólo en la sección Compañero': 'Only in the Buddy section',
  'En cualquier sección del panel': 'In any panel section',
  'Apretá «Definir atajo» y después una combinación de teclado o un botón lateral del mouse. No se captura mientras usás un campo, botón o enlace.':
    'Select “Set shortcut” and then press a key combination or a side mouse button. It is not captured while using a field, button or link.',
  'Algunos navegadores reservan los botones Atrás/Adelante del mouse; si no llegan a la página, usá una tecla o el cliente dani.py.':
    'Some browsers reserve the mouse Back/Forward buttons; if the page does not receive them, use a key or the dani.py client.',
  'Presioná la combinación… (Escape cancela)': 'Press the combination… (Escape cancels)',
  'Para todo el panel usá una combinación con Ctrl, Alt o Shift, o una tecla F1–F12.':
    'For the whole panel, use a combination with Ctrl, Alt or Shift, or an F1–F12 key.',
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
  'Modelos de texto': 'Text models',
  'Modelos de imagen': 'Image models',
  'Rendimiento': 'Performance',
  'Registros': 'Logs',
  'Configuración': 'Setup',
  'Buscar modelos nuevos': 'Scan for new models',
  'Vuelve a leer la carpeta de modelos y se lo avisa al servidor': 'Re-reads the model folder and tells the server',
  'Ajustes globales': 'Global settings',
  'Opciones extra globales': 'Global extra options',
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
  'Guardar prompt…': 'Save prompt…',
  'Guarda con un nombre el prompt que el modelo ya procesó (por ejemplo, las instrucciones y herramientas de Cline o de un asistente de voz). Al cargar el modelo se precargan todos: cada cliente arranca en ~1 s en vez de ~1 min.':
    "Saves under a name the prompt the model already processed (for example, Cline's or a voice assistant's instructions and tools). When the model loads all of them are preloaded: each client starts in ~1 s instead of ~1 min.",
  'Nombre para este prompt (por ejemplo cline o voz). Si ya existe, se reemplaza:': 'Name for this prompt (for example cline or voice). If it exists, it is replaced:',
  'Compartir ubicación aproximada con el compañero': 'Share approximate location with the buddy',
  '¿Borrar el prompt guardado «{name}»?': 'Delete the saved prompt «{name}»?',
  'Prompt guardado «{name}»: {size}, del {date}. Se precarga al cargar el modelo.': 'Saved prompt «{name}»: {size}, from {date}. It is preloaded when the model loads.',
  'Borrar': 'Delete',
  'Borrar prompt': 'Delete prompt',
  'Prompt guardado · {size}': 'Saved prompt · {size}',
  'Guardado el {date} ({size}). Clic en "Prompt guardado" lo reemplaza por lo que el modelo tiene procesado ahora.':
    'Saved on {date} ({size}). Clicking "Saved prompt" replaces it with what the model has processed now.',
  'Cargar': 'Load',
  'Cargando…': 'Loading…',
  'Descargar': 'Unload',
  'Ajustes': 'Settings',
  'MTP (borradores por paso)': 'MTP (drafts per step)',
  'Normal': 'Normal',
  'Borradores por paso': 'Drafts per step',
  // campos de cada modelo (llama_panel/text_models.py FIELDS)
  'Capas en GPU': 'GPU layers',
  'Definido en opciones extra: {opt}': 'Set in the extra options: {opt}',
  'En modo IA lo define una opción extra: {opt}': 'In AI mode an extra option sets it: {opt}',
  'Expertos MoE en RAM': 'MoE experts in RAM',
  'Pedidos en paralelo': 'Parallel requests',
  'Caché KV': 'KV cache',
  'Tanda del prompt': 'Prompt batch',
  'Hilos de CPU': 'CPU threads',
  'global': 'global',
  'auto': 'auto',
  'Cuántos tokens recuerda la conversación. Más contexto usa más VRAM.': 'How many tokens the conversation remembers. More context uses more VRAM.',
  'auto: llama.cpp calcula cuántas entran en la VRAM. all o un número para forzarlo.': 'auto: llama.cpp works out how many fit in VRAM. all or a number to force it.',
  'Para modelos MoE más grandes que la VRAM: all (todos) o en cuántas capas dejar los expertos en la RAM.': 'For MoE models larger than VRAM: all, or in how many layers to keep the experts in RAM.',
  '1 si lo usa una sola persona: todo el contexto para un pedido y más rápido en MoE grandes.': '1 if a single person uses it: all the context for one request, and faster on large MoE models.',
  'por defecto (f16)': 'default (f16)',
  'q8_0 (la mitad de memoria)': 'q8_0 (half the memory)',
  'q4_0 (un cuarto, pierde algo de calidad)': 'q4_0 (a quarter, some quality loss)',
  'Memoria del contexto. q8_0 casi no cambia la calidad y deja usar el doble de contexto.': 'Context memory. q8_0 barely changes quality and allows twice the context.',
  'Hace falta para comprimir la caché KV. auto suele andar bien.': 'Needed to compress the KV cache. auto usually works.',
  'por defecto (512)': 'default (512)',
  'Más grande: el prompt se procesa más rápido, a cambio de VRAM.': 'Larger: the prompt is processed faster, at the cost of VRAM.',
  'Normalmente auto. Importa cuando parte del modelo corre en el CPU.': 'Usually auto. It matters when part of the model runs on the CPU.',
  'Predicción de varios tokens por paso, si el modelo la trae: suele dar +20–30 % de velocidad.': 'Multi-token prediction, if the model includes it: usually +20–30 % speed.',
  'Tokens que adivina el MTP por paso. 2 suele ser el mejor.': 'Tokens the MTP guesses per step. 2 is usually best.',
  'Si el modelo piensa antes de responder (en los que lo permiten).': 'Whether the model thinks before answering (on models that support it).',
  'por defecto': 'default',
  'igual que normal': 'same as normal',
  'activado': 'on',
  'apagado': 'off',
  'En modo IA el escritorio está apagado y quedan ~0.4 GB más de VRAM: sirve, por ejemplo, para más contexto. Lo que quede en «igual que normal» usa la columna Normal.':
    'In AI mode the desktop is off and there is ~0.4 GB more VRAM: useful, for example, for more context. Anything left as «same as normal» uses the Normal column.',
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
  // audio
  'Audio': 'Audio',
  'whisper.cpp · Piper · CPU': 'whisper.cpp · Piper · CPU',
  'Texto e imágenes comparten la VRAM: al encender uno, el otro se apaga solo. El audio corre en el CPU y está siempre disponible.':
    'Text and images share the VRAM: starting one stops the other. Audio runs on the CPU and is always available.',
  'Modelos de audio': 'Audio models',
  'Oído': 'Hearing',
  'Voz': 'Voice',
  'Último uso': 'Last use',
  'Escuchar la voz': 'Hear the voice',
  'Escuchar': 'Listen',
  'En uso': 'In use',
  'Listo': 'Ready',
  'Incompleto': 'Incomplete',
  'Sin instalar': 'Not installed',
  'falta Piper': 'Piper missing',
  'falta whisper.cpp': 'whisper.cpp missing',
  'oído {s} s': 'heard {s} s',
  'voz {s} s': 'voice {s} s',
  'Oído: Whisper': 'Hearing: Whisper',
  'Voz: Piper': 'Voice: Piper',
  'Pasa tu voz a texto en el CPU. small es rápido (~0.7 s para 4 s de audio); large-v3-turbo entiende mejor pero tarda ~3 veces más.':
    'Turns your voice into text on the CPU. small is fast (~0.7 s for 4 s of audio); large-v3-turbo understands better but takes ~3 times longer.',
  'Lee las respuestas en voz alta, en el CPU (~0.2 s por oración).': 'Reads the answers aloud, on the CPU (~0.2 s per sentence).',
  'No hay modelos <code>ggml-*.bin</code> de whisper.cpp.': 'No whisper.cpp <code>ggml-*.bin</code> models.',
  'No hay voces de Piper (<code>.onnx</code> con su <code>.onnx.json</code>).': 'No Piper voices (<code>.onnx</code> with its <code>.onnx.json</code>).',
  'Carpetas: {dirs}. Otras se agregan con <code>LLAMA_AUDIO_DIRS</code> en paths.env.': 'Folders: {dirs}. Add others with <code>LLAMA_AUDIO_DIRS</code> in paths.env.',
  'Otros programas pueden usar esta voz con la API de OpenAI: <code>base_url</code> <code>http://{host}:{port}/v1</code>, rutas <code>/audio/speech</code> y <code>/audio/transcriptions</code>.':
    'Other programs can use this voice through the OpenAI API: <code>base_url</code> <code>http://{host}:{port}/v1</code>, routes <code>/audio/speech</code> and <code>/audio/transcriptions</code>.',
  'El navegador no dejó reproducir el audio.': 'The browser did not allow playing the audio.',
  // compañero
  'Compañero': 'Buddy',
  '<code>{name}</code> se reemplaza por el nombre. Se lee en voz alta: conviene pedirle respuestas cortas.':
    '<code>{name}</code> is replaced by the name. It is read aloud: ask for short answers.',
  'Ajustes del compañero': 'Buddy settings',
  'Callar': 'Hush',
  'Charla nueva': 'New chat',
  'Decile hola a {name}.': 'Say hi to {name}.',
  'El navegador solo deja usar el micrófono en una página segura. Abrí el panel por un túnel SSH (<code>ssh -L 8090:localhost:8090 …</code>) y entrá a <code>http://localhost:8090</code>.':
    'Browsers only allow the microphone on a secure page. Open the panel through an SSH tunnel (<code>ssh -L 8090:localhost:8090 …</code>) and go to <code>http://localhost:8090</code>.',
  'El que esté cargado': 'Whichever is loaded',
  'Enviar': 'Send',
  'Escribile algo…': 'Write something…',
  'Guardar memoria': 'Save memory',
  'Hablar en voz alta': 'Speak aloud',
  'Herramientas': 'Tools',
  'Lo que anota con «recordar». Se lee al empezar cada charla.': 'What it notes with "recordar". Read at the start of each chat.',
  'Mantené apretado para hablar (o la barra espaciadora)': 'Hold to talk (or the space bar)',
  'Memoria': 'Memory',
  'No hay permiso para usar el micrófono.': 'No permission to use the microphone.',
  'Nombre': 'Name',
  'Personalidad': 'Personality',
  'Idioma de la charla': 'Chat language',
  'Instrucciones': 'Instructions',
  'Cada parte por separado: si no la tocás, usa la de fábrica (y recibe sus mejoras). <code>{name}</code> se reemplaza por el nombre. Las fijas dependen del panel y no se editan.':
    'Each part on its own: untouched parts use the default (and get its improvements). <code>{name}</code> is replaced by the name. Fixed parts depend on the panel and cannot be edited.',
  'Al guardar un cambio, la próxima respuesta tarda unos 20 s más: el modelo vuelve a leer todas las instrucciones.':
    'After saving a change, the next reply takes about 20 s longer: the model rereads all the instructions.',
  'Volver a la de fábrica': 'Restore default',
  'fija': 'fixed',
  'modificada': 'edited',
  'Guardar y probar': 'Save and test',
  'Guarda y le hace 10 preguntas de prueba al modelo cargado (~1 min): si consulta cuando tiene que consultar y no inventa.':
    'Saves and asks the loaded model 10 test questions (~1 min): does it look things up when it should, without making things up.',
  'Probando… {done} de {total}': 'Testing… {done} of {total}',
  '{ok} de {total} bien': '{ok} of {total} passed',
  'esperaba': 'expected',
  'hizo': 'did',
  'Oído': 'Hearing',
  'Vocabulario que recibe Whisper para escribir bien los nombres. Se arma solo: el nombre del compañero, tu zona, los lugares que consultaste, Home Assistant, la memoria y lo aprendido.':
    'Vocabulary Whisper gets so it spells names right. It builds itself: the buddy\'s name, your area, places you asked about, Home Assistant, memory and what it learned.',
  'Guardar mis últimos 50 audios (solo en esta máquina) para comparar con y sin vocabulario':
    'Keep my last 50 recordings (only on this machine) to compare with and without vocabulary',
  'Comparar ahora': 'Compare now',
  'Borrar audios': 'Delete recordings',
  'Correcciones aprendidas: cuando el modelo entiende un nombre que Whisper escribió mal, lo anota; a la segunda vez se aplica sola.':
    'Learned corrections: when the model understands a name Whisper got wrong, it notes it; the second time it applies automatically.',
  'Todavía ninguna.': 'None yet.',
  'En espera (vistas una vez):': 'Waiting (seen once):',
  'Comparando…': 'Comparing…',
  '{n} audios guardados.': '{n} recordings saved.',
  '{n} audios guardados. Última comparación ({m} audios): cambió en {changed}; nombres del vocabulario {sin} sin y {con} con.':
    '{n} recordings saved. Last comparison ({m} recordings): changed in {changed}; vocabulary names {sin} without and {con} with.',
  'sin': 'without',
  'con': 'with',
  'Whisper escribió: {raw}': 'Whisper wrote: {raw}',
  'Aprendí cómo se escribe un nombre que no entendí bien.': 'I learned how to spell a name I misheard.',
  'escuchando…': 'listening…',
  'oído en {s} s': 'heard in {s} s',
  'oído: Whisper': 'hearing: Whisper',
  'primera palabra a los {s} s': 'first word at {s} s',
  'sin oído (falta whisper.cpp)': 'no hearing (whisper.cpp missing)',
  'sin voz (falta Piper)': 'no voice (Piper missing)',
  'voz: Piper': 'voice: Piper',
  'solo mira': 'read only',
  'escribe su memoria': 'writes its memory',
  'cambia cosas': 'changes things',
  'peligrosa': 'dangerous',
  'Vos': 'You',
  'Cancelar': 'Cancel',
  'Ningún aviso pendiente. Pedile «avisame en 20 minutos…».': 'No pending reminders. Ask "remind me in 20 minutes…".',
  'Lo dijo por su cuenta': 'Said on its own',
  'Por su cuenta': 'On its own',
  'Avisar si la GPU o la CPU calientan, se cae un servicio o falta RAM o disco': 'Warn if the GPU or CPU run hot, a service fails, or RAM or disk run low',
  'Resumen de la mañana a las': 'Morning summary at',
  'El resumen lo escribe el modelo si ya hay uno cargado; si no, uno corto con los datos. Vacío = sin resumen.':
    'The model writes the summary if one is already loaded; otherwise a short one from the data. Empty = no summary.',
  'Avisos pendientes': 'Pending reminders',
  'Si el panel tiene contraseña, agregá <code>--token</code> con uno creado en Configuración, Acceso.':
    'If the panel has a password, add <code>--token</code> with one created in Setup, Access.',
  'Hablar desde cualquier programa de tu PC': 'Talk from any program on your PC',
  'El atajo de arriba funciona sólo cuando llama-panel tiene el foco. Para usar una tecla sobre cualquier programa, descargá este cliente en la PC que tiene el micrófono y dejalo corriendo. En Windows, abrí PowerShell:':
    'The shortcut above works only while llama-panel has focus. To use a key over any program, download this client on the PC with the microphone and leave it running. On Windows, open PowerShell:',
  'Mientras mantenés F9 apretado graba; al soltarlo manda el audio. Podés cambiar <code>f9</code> por <code>ctrl_r</code>, otra tecla F o una letra.':
    'It records while you hold F9 and sends the audio when you release it. You can replace <code>f9</code> with <code>ctrl_r</code>, another F key or a letter.',
  'Te escucho…': 'Listening…',
  'Pensando…': 'Thinking…',
  'Escuchando lo que dijiste…': 'Understanding what you said…',
  'token de acceso de larga duración': 'long-lived access token',
  'En Home Assistant: tu perfil, Seguridad, «Tokens de acceso de larga duración». Mejor de un usuario que no sea administrador. Queda en un archivo aparte, solo legible por tu usuario.':
    'In Home Assistant: your profile, Security, "Long-lived access tokens". Preferably from a non-admin user. It is kept in a separate file only your user can read.',
  'Guardar token': 'Save token',
  'sin conectar': 'not connected',
  'conectado': 'connected',
  'no disponible': 'not available',
  // acceso
  'Panel con contraseña': 'Password-protected panel',
  'Contraseña': 'Password',
  'Entrar': 'Log in',
  'Acceso': 'Access',
  'El panel pide contraseña en la red. Desde esta máquina (y por túnel SSH) no la pide.':
    'The panel asks for a password on the network. It does not ask from this machine (or through an SSH tunnel).',
  'Sin contraseña: cualquiera en tu red puede usar el panel y hablarle al compañero (que puede manejar luces). Los puertos 8080 y 5001 no pasan por acá.':
    'No password: anyone on your network can use the panel and talk to the buddy (which can control lights). Ports 8080 and 5001 do not go through here.',
  'Contraseña actual': 'Current password',
  'Contraseña nueva (vacía para sacarla)': 'New password (empty to remove it)',
  'Contraseña nueva': 'New password',
  'Repetila': 'Repeat it',
  'Cambiar contraseña': 'Change password',
  'Poner contraseña': 'Set password',
  'Cerrar sesión': 'Log out',
  'Tokens para programas': 'Tokens for programs',
  'Para <code>dani.py --token …</code> o un cliente de la API de OpenAI (<code>api_key</code>). Cada programa con el suyo, así se puede revocar uno solo.':
    'For <code>dani.py --token …</code> or an OpenAI API client (<code>api_key</code>). One per program, so each can be revoked on its own.',
  'Revocar': 'Revoke',
  'Ninguno todavía.': 'None yet.',
  'nombre, ej.: laptop': 'name, e.g. laptop',
  'Crear token': 'Create token',
  'Las dos contraseñas no coinciden.': 'The two passwords do not match.',
  // encendido a demanda
  'Encendido a demanda': 'On-demand start',
  'Prender texto o imágenes según lo que pida cada cliente': 'Start text or images depending on what each client asks for',
  'El panel atiende los puertos {chat} y {img}: un pedido de imagen con un modelo de texto cargado lo baja y prende el generador, y al revés, siempre después de que terminen los pedidos en curso. Los clientes no cambian de dirección. Al activarlo o desactivarlo se reinician los servicios encendidos. Si el panel se reinicia, se corta lo que esté pasando por él.':
    'The panel serves ports {chat} and {img}: an image request while a text model is loaded unloads it and starts the generator, and the other way around, always after the requests in progress finish. Clients keep the same address. Turning it on or off restarts the running services. If the panel restarts, whatever is going through it is cut off.',
  'Descargar después de estos minutos sin uso': 'Unload after this many idle minutes',
  '0 = nunca': '0 = never',
  'Baja el modelo de texto o apaga el generador de imágenes para liberar la VRAM. Funciona también sin el encendido a demanda.':
    'Unloads the text model or stops the image generator to free the VRAM. Also works without on-demand start.',
  'Atendiendo los puertos {chat} y {img}.': 'Serving ports {chat} and {img}.',
  'Todavía no atiende los puertos.': 'Not serving the ports yet.',
  '{n} pedidos de texto en curso': '{n} text requests in progress',
  '{n} pedidos de imagen en curso': '{n} image requests in progress',
  '{n} de texto esperando': '{n} text waiting',
  '{n} de imagen esperando': '{n} image waiting',
  // varios
  'carga {load}': 'load {load}',
  'ventilador {pct} %': 'fan {pct} %',
  'hace {time}': '{time} ago',
  'No se pudo contactar al panel.': 'Could not reach the panel.',
};
