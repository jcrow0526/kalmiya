<p align="center"><img src="docs/assets/icon.png" width="110" alt="Kalmiya"></p>
<h1 align="center">Kalmiya</h1>
<p align="center">
  <b>Asistente de voz para Windows y Linux, 100 % local.</b><br>
  <i>A local, private voice assistant for Windows — Spanish &amp; English.</i><br><br>
  <a href="docs/">Landing page</a> ·
  <a href="https://github.com/jcrow0526/kalmiya/releases/latest/download/KalmiyaSetup.exe">Descargar para Windows</a> ·
  <a href="https://github.com/jcrow0526/kalmiya/releases/latest">Videos de presentación</a>
</p>
<p align="center"><img src="docs/assets/poster_es.jpg" width="720" alt="Hola, soy Kalmiya"></p>

> **EN** — Hold **Ctrl+Alt+Space**, talk, release. Kalmiya transcribes your voice with Whisper, decides what to do
> with Liquid AI's **LFM3** and answers out loud with Piper. Everything runs on your PC: no account, no cloud.
> It plays music, opens apps, closes tabs, manages your calendar and reminders, reads your news, writes WhatsApp
> messages (always asking before sending) and answers questions about images on your screen (LFM3‑VL).

Mantén **Ctrl+Alt+Espacio**, habla, suelta. Kalmiya transcribe tu voz con Whisper, decide qué hacer
con **LFM3‑1.7B** (Liquid AI) y te responde en voz alta con Piper (Daniela, español latino; o inglés).
Todo corre en tu PC, sin nube.

Inspirado en [Handy](https://github.com/cjpais/Handy): también trae **dictado** (Ctrl+Alt+D)
que pega el texto donde esté el cursor.

## Instalar (un clic)

Doble clic en **`Instalar.bat`**: abre la ventana del instalador (orbe animado, progreso con los MB
descargados). Por dentro ([installer/install.ps1](installer/install.ps1)):

1. Instala `uv` y un Python 3.12 aislado (no toca el Python del sistema).
2. Descarga `llama-server` de llama.cpp (CUDA si detecta NVIDIA, si no CPU).
3. Descarga LFM3‑1.7B (~1 GB), LFM3‑VL‑2B para imágenes (~1.2 GB, opcional), Whisper y las voces
   (Daniela es_AR, Claude es_MX, Lessac en_US). Con NVIDIA usa Whisper `large-v3-turbo`; sin GPU, `small`.
4. Crea accesos en Inicio, Escritorio y arranque automático.

Todo queda en `%LOCALAPPDATA%\Kalmiya` (una instalación anterior de *Eco* se migra sola, con sus
modelos). Para quitarlo: menú Inicio → *Desinstalar Kalmiya*. En consola:
`installer\install.ps1 -Gpu vulkan` (AMD/Intel), `-Quant Q8_0`, `-NoStartup`, `-NoVision`, `-Nemotron`.

La primera vez, Kalmiya pregunta el idioma (español o inglés), deja escuchar las voces y presenta
lo que puede hacer (icono → *Ver presentación* para repetirla).

### Linux (Wayland + Hyprland)

```
./Instalar.sh            # opciones: --gpu cuda|vulkan|cpu, --quant Q8_0, --no-vision, --no-startup
```

Probado en [Omarchy](https://omarchy.org) (Arch + Hyprland con configuración en Lua). El instalador
([installer/install.sh](installer/install.sh)) instala lo que falte con pacman (`wtype`, `wl-clipboard`,
`grim`, `slurp`, `tesseract`, `gtk4-layer-shell`, `libayatana-appindicator`), crea un Python 3.12 aislado
con `uv`, descarga `llama-server` (CUDA 12 si hay NVIDIA), los mismos modelos y el OCR de Tesseract en
español, y deja todo en `~/.local/share/kalmiya`. Para quitarlo: *Desinstalar Kalmiya* en el lanzador
o `bash ~/.local/share/kalmiya/app/uninstall.sh`.

Qué cambia respecto a Windows ([kalmiya/linux/](kalmiya/linux/)):

- **Atajos:** Wayland no deja a una app leer el teclado global, así que son binds de Hyprland.
  Kalmiya escribe `~/.config/hypr/kalmiya.lua` (cargado desde `hyprland.lua`) y los binds le avisan
  con `kalmiya-ctl` por un socket local. Para cambiarlos: *Configuración → General* (se escriben a mano,
  p. ej. `ctrl+alt+space`).
- **Orbe y burbuja:** un proceso con GTK 4 y gtk4-layer-shell los muestra como capa *overlay* que no
  recibe clics ni roba el foco; se dibujan con el mismo código que en Windows.
- **Ventanas y teclas:** `hyprctl` (ventana activa, enfocar, cerrar, pantalla completa), `wtype` para
  teclas y dictado (escribe directamente, también en terminales) y un ratón virtual (uinput) para los
  clics sobre los recomendados de YouTube o la lista de WhatsApp Web.
- **URL del navegador:** sin UI Automation, se copia de la barra (Ctrl+L, Ctrl+C, Esc) al soltar el
  atajo, solo si el navegador es la ventana activa. Las pestañas que no están a la vista se buscan con
  el buscador de pestañas del navegador (Ctrl+Shift+A en Chromium).
- **Medios y volumen:** MPRIS (`playerctl` o `busctl`) y `wpctl`; bajar las demás apps, con `pactl`.
- **WhatsApp:** la web app de WhatsApp (la de Omarchy u otra ventana de WhatsApp Web).
- **Si la GPU está llena** (otro modelo cargado en LM Studio, por ejemplo), Whisper y LFM3 pasan solos
  a la CPU: funciona, pero tarda varios segundos por orden.

**Con Omarchy** Kalmiya maneja también el escritorio ([kalmiya/linux/omarchy.py](kalmiya/linux/omarchy.py)):

| Di… | Hace |
|---|---|
| "Ve al escritorio 3", "manda esta ventana al 2", "flota esta ventana", "cierra todas las ventanas" | Escritorios y ventanas de Hyprland |
| "Cambia el tema a Tokyo Night", "pon un tema claro", "pon otro tema", "cambia el fondo", "quita la barra" | Temas, fondos, transparencia, márgenes y barra |
| "Activa la luz nocturna", "modo no molestar", "mantén la pantalla encendida", "apaga el Bluetooth", "modo ahorro" | Ajustes rápidos |
| "Sube el brillo", "brillo al 40 por ciento" | Brillo (también monitores externos por DDC) |
| "Haz una captura de una zona", "copia el texto de la pantalla", "copia un color", "lee el código QR" | Capturas, OCR y selector de color de Omarchy |
| "Graba la pantalla con audio", "detén la grabación" | Grabación de pantalla |
| "Abre el menú", "abre el wifi", "enséñame los atajos", "abre el portapapeles", "administrador de tareas" | Menús y paneles de Omarchy |
| "Borra las notificaciones" | Notificaciones |
| "¿Cuánta memoria uso?", "¿cuánto disco me queda?", "¿qué IP tengo?", "¿hay actualizaciones?" | Estado del equipo |
| "Actualiza el sistema", "apaga / reinicia / suspende la computadora" | Siempre pregunta antes |

Órdenes desde la terminal: `kalmiya settings`, `kalmiya calendar`, `kalmiya reminders`, `kalmiya quit`.

### Landing page

Está en [docs/](docs/) (HTML estático, sin dependencias). Para hospedarla con Docker o
[Easypanel](https://easypanel.io): crea una App desde este repositorio, tipo de build **Dockerfile** con
ruta **`docs`**, y expón el puerto **80**. Los recursos (orbe, videos, portadas) se regeneran con
`python docs/make_assets.py`.

### Generar un `KalmiyaSetup.exe`

Con [Inno Setup 6](https://jrsoftware.org/isinfo.php): `iscc installer\kalmiya.iss` → `dist\KalmiyaSetup.exe`.

## Velocidad (RTX 4070 Laptop 8 GB, Ryzen 7 7840HS)

De soltar el atajo a oír la respuesta: **~0.56 s** (Whisper 0.23 s + LFM3 0.23 s + primer audio 0.09 s);
LFM3 elige la herramienta correcta en 24 de 24 órdenes de prueba; LFM3‑VL responde sobre una imagen en 0.23 s.

**Mide tu equipo:** menú Inicio → *Kalmiya Benchmark* (o `python -m kalmiya.bench`) y comparte el resultado.
Detalles y tabla de la comunidad en [bench/](bench/README.md) · [resultados](bench/RESULTS.md).

| Voz a texto (5 frases en español) | Latencia/frase | Aciertos |
|---|---|---|
| **Whisper large‑v3‑turbo, GPU** (predeterminado) | 0.2 s | 5/5 |
| Whisper small, GPU | 0.1 s | 3/5 ("Diogo Tube", "Kube") |
| Nemotron 3.5 ASR 0.6B, GPU | 0.03 s | 3/5 ("Bad Booney en octubre") |
| Parakeet TDT 0.6B v3, GPU | 0.02 s | 4/5 ("Bad Bundy en Diobtube") |
| Nemotron 3.5 Transcribe 30B‑A4B (IQ4_NL), CPU | 4–6 s | 5/5 (no cabe en 8 GB de VRAM) |
| Whisper large‑v3‑turbo, CPU | 4.4 s | 5/5 |

Para usar Nemotron: *Configuración → Motor de voz a texto → nemotron* y en *Modelo Nemotron* un alias
(`nemotron-3.5`, `parakeet-tdt`) o un GGUF en `%LOCALAPPDATA%\Kalmiya\models\nemotron`.

## Qué le puedes pedir

| Ejemplo | Herramienta |
|---|---|
| “Pon Bad Bunny”, “ponme un video de cómo hacer tacos” | `reproducir` (primer resultado de YouTube, en la pestaña de YouTube que ya tengas; si estás en Spotify, busca ahí) |
| “Abre el primer recomendado”, “pon el segundo de la derecha”, “ponme el de Escocia” | `youtube_recomendado` / `reproducir` (lee *tu* columna de recomendados con el OCR de Windows y hace clic) |
| “Cierra esta pestaña”, “regresa”, “pantalla completa”, “minimiza esto” | `controlar_ventana` (sobre la ventana activa) |
| “Cierra la pestaña de YouTube”, “cierra la de los drones”, “ve a la pestaña de Gmail” | `pestana` (busca por título en todas las ventanas del navegador con UI Automation; cierra con su ✕ sin cambiar la que ves) |
| “¿Cómo está el clima?”, “¿va a llover mañana en Madrid?” | `clima` (Open‑Meteo, sin clave) |
| “¿Quién es Bad Bunny?”, “¿cuál es la capital de Australia?” | `buscar_info` (Wikipedia, resumido por LFM3) |
| “¿Cuánto es 20 por 8?” | `responder` |
| “Ponme dentista el viernes a las 4” | `crear_evento` (calendario de Kalmiya, y Google Calendar por API si lo conectas) |
| “¿Qué tengo mañana?”, “abre mi calendario” | `ver_agenda`, ventana del calendario (avisa 10 min antes de cada evento) |
| **Ctrl+Alt+I** → clic en una imagen → “¿qué es esto?” | LFM3‑VL‑2B (se arranca al primer uso) |
| **Ctrl+Alt+N** | nueva conversación |
| “Abre YouTube” / “busca recetas de mole” | `abrir_web`, `buscar_web` |
| “Descarga este video” / “baja la canción X en mp3” | `descargar_video` (yt‑dlp; “este” = la pestaña abierta) |
| “Abre Spotify”, “cierra Spotify” | `abrir_aplicacion` (si ya está abierto lo trae al frente), `cerrar_aplicacion` |
| “Siguiente canción”, “sube el volumen”, “volumen al 50 %” | `controlar_medios` (porcentaje exacto con `fijar_volumen`) |
| “Abre la conversación de mamá” | `whatsapp_abrir_chat` (app de WhatsApp; lee la lista con OCR o usa su buscador) |
| “Mándale a mamá que ya voy”, “dile a Ana que…” | `whatsapp_mensaje` (lo escribe y **pregunta siempre antes de enviar**; “no” lo borra, callar lo deja escrito) |
| “Recuérdame sacar la ropa en 20 minutos / mañana a las 9” | `recordatorio` (se guarda en el calendario, sobrevive a reinicios) |
| “¿Qué recordatorios tengo?”, “abre mis recordatorios” | `ver_recordatorios`, `abrir_recordatorios` (ventana para añadir/borrar) |
| “Resúmeme las noticias”, “¿qué hay de deportes?”, “¿qué dice Xataka?” | `resumen_noticias` (RSS de tus periódicos; se añaden en *Configuración → Noticias*, con recomendados) |
| “Toma una captura”, “abre descargas”, “bloquea la PC” | `captura_pantalla`, `abrir_carpeta`, `bloquear_pc` |

Añadir una herramienta = una función con `@tool(...)` en [kalmiya/tools.py](kalmiya/tools.py).

**Contexto:** al pulsar el atajo, Kalmiya anota la ventana activa, la URL de la pestaña del navegador
(por UI Automation) y las demás apps abiertas ([kalmiya/context.py](kalmiya/context.py)), y se lo da al modelo.

**Baja el volumen mientras hablas:** las demás apps quedan al 15 % desde que pulsas el atajo
hasta que Kalmiya termina de responder.

**Cuando duda, pregunta:** si Whisper entendió con poca confianza (“¿Dijiste: …?”), si el video
encontrado no se parece a lo pedido, o si la app solo coincide aproximadamente. Tras la pregunta
escucha la respuesta sin pulsar el atajo: “sí” lo hace, “no” lo cancela, callar lo deja.

**Interfaz:** un orbe 3D semitransparente en el centro derecho (se mueve con tu voz; color en
*Configuración*) y una burbuja de vidrio esmerilado (“liquid glass”) con la respuesta, que se aclara u
oscurece según lo que haya detrás. Son ventanas por capas de Windows dibujadas con numpy/PIL
([kalmiya/orb.py](kalmiya/orb.py), [kalmiya/glass.py](kalmiya/glass.py)).

**Google Calendar:** calendario → *Conectar Google Calendar*. Pide una vez el JSON de un cliente OAuth
tipo “App de escritorio” (Google Cloud → Calendar API) y abre el navegador para autorizar.

**Inglés:** se elige en la presentación o en *Configuración → Idioma*. Whisper escucha en inglés, la voz
es Lessac y las respuestas se traducen con LFM3 (~0.2 s extra).

## Arquitectura

```
atajo (keyboard) → micrófono (sounddevice) → faster-whisper → LFM3 vía llama-server (OpenAI API + tools)
                                                                   ↓ tool calls (XML de LFM3)
                                     kalmiya/tools.py → respuesta → Piper TTS + orbe y burbuja de vidrio
```

- [kalmiya/app.py](kalmiya/app.py) orquesta; [kalmiya/llm.py](kalmiya/llm.py) arranca llama-server y corre el bucle de agente.
- Si ya usas Ollama o LM Studio, pon su URL en *Configuración → Servidor LLM externo*.
- Configuración: `%LOCALAPPDATA%\Kalmiya\config.json`. Registros: `%LOCALAPPDATA%\Kalmiya\logs`.

## Desarrollo

```
uv venv -p 3.12 && uv pip install -r requirements.txt
python -m kalmiya
```

## Licencia

Código bajo [licencia MIT](LICENSE). Los modelos no se incluyen en el repositorio: el instalador los descarga
de sus autores y cada uno tiene su propia licencia — LFM3 y LFM3‑VL ([LFM Open License](https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF)),
Whisper (MIT), voces de [Piper](https://huggingface.co/rhasspy/piper-voices) (licencia de cada voz) y
[llama.cpp](https://github.com/ggml-org/llama.cpp) (MIT).
