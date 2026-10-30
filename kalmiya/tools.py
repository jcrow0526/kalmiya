"""Herramientas que el modelo puede invocar para controlar la PC.

Cada herramienta es una función decorada con @tool; su docstring y la
anotación de parámetros se convierten en el esquema JSON que recibe el LLM.
"""
import datetime as dt
import difflib
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import urllib.parse
import uuid
import webbrowser
from pathlib import Path
from typing import Callable

from . import LINUX, context
from .calendar_store import Calendar
from .config import DOWNLOADS_DIR, EVENTS_DIR, Config

log = logging.getLogger(__name__)

TOOLS: dict[str, dict] = {}


def tool(description: str, params: dict | None = None, required: list[str] | None = None, direct: bool = True):
    """Registra una función como herramienta. `params` = {nombre: (tipo, descripción)}.

    `direct=True`: su resultado se dice tal cual. `direct=False`: devuelve datos que el
    modelo debe convertir en respuesta (p.ej. resultados de búsqueda).
    """
    def deco(fn: Callable):
        props = {}
        for name, spec in (params or {}).items():
            typ, desc = spec[0], spec[1]
            p = {"type": typ, "description": desc}
            if len(spec) > 2:
                p["enum"] = spec[2]
            props[name] = p
        TOOLS[fn.__name__] = {
            "fn": fn,
            "direct": direct,
            "schema": {
                "type": "function",
                "function": {
                    "name": fn.__name__,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required if required is not None else list(props),
                    },
                },
            },
        }
        return fn
    return deco


class Context:
    """Lo que las herramientas necesitan de la app (config, voz, notificaciones)."""
    config: Config = Config()
    notify: Callable[[str], None] = staticmethod(lambda msg: None)
    speak: Callable[[str], None] = staticmethod(lambda msg: None)
    snapshot: "context.Snapshot | None" = None  # lo que el usuario tenía abierto al hablar
    pending: "Pending | None" = None  # acción esperando un "sí" del usuario
    calendar: "Calendar | None" = None  # calendario de Kalmiya (lo crea la app)
    open_calendar: Callable[[], None] = staticmethod(lambda: None)  # muestra la ventana del calendario
    open_reminders: Callable[[], None] = staticmethod(lambda: None)  # muestra la lista de recordatorios
    # Acciones que necesitan la pantalla sin el orbe (capturas, apagar…): la app las ejecuta
    # cuando Kalmiya termina de hablar y lo oculta.
    later: list[Callable[[], None]] = []


ctx = Context()


class Pending:
    """Acción que espera confirmación: `accion` si dice que sí, `al_cancelar` si dice que no."""

    def __init__(self, accion: Callable[[], str], al_cancelar: Callable[[], str] | None = None):
        self.accion = accion
        self.al_cancelar = al_cancelar

    def __call__(self) -> str:
        return self.accion()

    def cancel(self) -> str:
        return self.al_cancelar() if self.al_cancelar else "Vale, no lo hago."


def ask(pregunta: str, accion: Callable[[], str], siempre: bool = False,
        al_cancelar: Callable[[], str] | None = None) -> str:
    """Pregunta antes de actuar. El agente guarda `accion` y la ejecuta si el usuario responde que sí.

    `siempre=False`: solo cuando la herramienta duda de haber entendido (se puede desactivar).
    `siempre=True`: acciones en nombre del usuario, como enviar un mensaje."""
    if not siempre and not ctx.config.confirmar_si_dudo:
        return accion()
    ctx.pending = Pending(accion, al_cancelar)
    return pregunta


def _norm(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9ñ]+", s)


def _match(consulta: str, encontrado: str) -> float:
    """Qué parte de las palabras pedidas aparece (aproximadamente) en lo encontrado."""
    pedidas = [w for w in _norm(consulta) if len(w) > 2 and w not in {"video", "cancion", "musica", "de", "del", "las", "los"}]
    if not pedidas:
        return 1.0
    hay = _norm(encontrado)
    ok = sum(1 for w in pedidas if any(difflib.SequenceMatcher(None, w, h).ratio() >= 0.75 for h in hay))
    return ok / len(pedidas)


def _active() -> "context.Window | None":
    return ctx.snapshot.activa if ctx.snapshot else None


# ---------------------------------------------------------------- plataforma
# Lo mismo en Windows (keyboard, pyperclip, os.startfile, win32gui) y en Linux (kalmiya/linux/desktop.py).

def _send(combo: str) -> None:
    if LINUX:
        from .linux import desktop
        desktop.send(combo)
    else:
        import keyboard
        keyboard.send(combo)


def _clip_get() -> str | None:
    if LINUX:
        from .linux import desktop
        return desktop.clipboard_get()
    import pyperclip
    try:
        return pyperclip.paste()
    except Exception:
        return None


def _clip_set(texto: str) -> None:
    if LINUX:
        from .linux import desktop
        desktop.clipboard_set(texto)
    else:
        import pyperclip
        pyperclip.copy(texto)


def _startfile(target: str) -> None:
    """Abre un archivo, carpeta o URL con su app predeterminada (OSError si no se puede)."""
    if LINUX:
        from .linux import desktop
        if not desktop.open_uri(str(target)):
            raise OSError(f"No sé abrir {target}")
    else:
        os.startfile(str(target))


def _rect(hwnd) -> tuple[int, int, int, int]:
    if LINUX:
        from . import screen
        return screen._rect(hwnd)
    import win32gui
    return win32gui.GetWindowRect(hwnd)


def _is_proc(w: "context.Window | None", name: str) -> bool:
    """¿La ventana es de ese programa? ("spotify" = spotify.exe en Windows, clase spotify en Linux)."""
    return bool(w) and w.proceso.removesuffix(".exe") == name


# ---------------------------------------------------------------- conversación

# LFM3-1.7B tiende a llamar *alguna* herramienta siempre; con esta, las preguntas
# (cálculos, fecha, datos) acaban en una respuesta hablada y no en una acción.
@tool("Responde al usuario en voz alta. Úsala para preguntas, cálculos, fecha/hora o charla.",
      {"texto": ("string", "La respuesta, una o dos frases cortas")})
def responder(texto: str) -> str:
    return texto


def _http_json(url: str, params: dict) -> dict:
    import httpx
    # Wikipedia rechaza (403) el "Accept-Encoding: gzip, deflate" que httpx manda por defecto.
    r = httpx.get(url, params=params, timeout=8,
                  headers={"User-Agent": "Kalmiya/0.1 (asistente de voz local)", "Accept-Encoding": "gzip"})
    r.raise_for_status()
    return r.json()


@tool("Busca información en internet (Wikipedia) para responder preguntas de datos: personas, lugares, "
      "historia, definiciones. Después responde con lo encontrado.",
      {"pregunta": ("string", "Tema a buscar, p.ej. 'capital de Francia'")}, direct=False)
def buscar_info(pregunta: str) -> str:
    lang = (ctx.config.idioma or "es")[:2]
    data = _http_json(f"https://{lang}.wikipedia.org/w/api.php", {
        "action": "query", "format": "json", "generator": "search", "gsrsearch": pregunta, "gsrlimit": 2,
        "prop": "extracts", "exintro": 1, "explaintext": 1, "exsentences": 4, "redirects": 1,
    })
    pages = sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
    if not pages:
        return f"No encontré información sobre {pregunta}"
    return "\n\n".join(f"{p['title']}: {p.get('extract', '')}" for p in pages)


_CLIMA = {0: "despejado", 1: "mayormente despejado", 2: "parcialmente nublado", 3: "nublado", 45: "con niebla",
          48: "con niebla", 51: "con llovizna", 53: "con llovizna", 55: "con llovizna", 61: "con lluvia ligera",
          63: "con lluvia", 65: "con lluvia fuerte", 71: "con nieve", 73: "con nieve", 75: "con nieve fuerte",
          80: "con chubascos", 81: "con chubascos", 82: "con chubascos fuertes", 95: "con tormenta",
          96: "con tormenta y granizo", 99: "con tormenta y granizo"}


@tool("Dice el clima actual y el pronóstico de hoy y mañana de una ciudad.",
      {"ciudad": ("string", "Ciudad que nombró el usuario (p.ej. 'Madrid'); vacío solo si no nombró ninguna"),
       "dia": ("string", "Qué día", ["hoy", "mañana"])},
      required=[])
def clima(ciudad: str = "", dia: str = "hoy") -> str:
    ciudad = (ciudad or ctx.config.ciudad).strip()
    if not ciudad:
        return "Error: no sé de qué ciudad. Pregunta al usuario la ciudad."
    geo = _http_json("https://geocoding-api.open-meteo.com/v1/search",
                     {"name": ciudad, "count": 1, "language": "es"}).get("results")
    if not geo:
        return f"Error: no encontré la ciudad {ciudad}"
    g = geo[0]
    w = _http_json("https://api.open-meteo.com/v1/forecast", {
        "latitude": g["latitude"], "longitude": g["longitude"], "timezone": "auto", "forecast_days": 2,
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
    })
    d = w["daily"]
    i = 1 if dia == "mañana" else 0
    pronostico = (f"máxima de {round(d['temperature_2m_max'][i])} y mínima de {round(d['temperature_2m_min'][i])} grados, "
                  f"{_CLIMA.get(d['weather_code'][i], 'variable')}, "
                  f"{d['precipitation_probability_max'][i]} por ciento de probabilidad de lluvia")
    if i == 1:
        return f"Mañana en {g['name']}: {pronostico}"
    c = w["current"]
    return (f"En {g['name']} hay {round(c['temperature_2m'])} grados, {_CLIMA.get(c['weather_code'], 'variable')}. "
            f"Hoy: {pronostico}")


# ---------------------------------------------------------------- música y videos

def _youtube_first(consulta: str) -> tuple[str, str, str] | None:
    """Primer resultado de YouTube (id, título, canal) sin descargar nada."""
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch1:{consulta}", download=False)
    entries = (info or {}).get("entries") or []
    if not entries:
        return None
    e = entries[0]
    return e["id"], e.get("title") or consulta, e.get("channel") or e.get("uploader") or ""


def _youtube_related(video_id: str) -> list[tuple[str, str]]:
    """Videos de la columna "recomendados" de un video (vista sin sesión iniciada)."""
    import json

    import httpx
    r = httpx.get(f"https://www.youtube.com/watch?v={video_id}", timeout=10, follow_redirects=True,
                  cookies={"CONSENT": "YES+1"},
                  headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                         "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
                           "Accept-Language": "es-MX,es;q=0.9"})
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", r.text)
    if not m:
        return []
    out = []

    def walk(o):
        if isinstance(o, dict):
            lv = o.get("lockupViewModel")
            if lv and lv.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO":
                title = lv.get("metadata", {}).get("lockupMetadataViewModel", {}).get("title", {}).get("content", "")
                out.append((lv["contentId"], title))
            v = o.get("compactVideoRenderer")  # formato anterior
            if v:
                out.append((v["videoId"], "".join(r.get("text", "") for r in v.get("title", {}).get("runs", []))
                            or v.get("title", {}).get("simpleText", "")))
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    secondary = json.loads(m.group(1)).get("contents", {}).get("twoColumnWatchNextResults", {}).get("secondaryResults")
    walk(secondary or {})
    return out


def _youtube_tab() -> "context.Window | None":
    """Pestaña de YouTube que el usuario tiene a la vista (para no abrir otra)."""
    b = ctx.snapshot.browser() if ctx.snapshot else None
    return b if b and "youtube.com" in b.url else None


@tool("Reproduce música o un video: busca la canción, artista o video y lo pone a sonar. "
      "Usa esto para 'pon', 'reproduce', 'quiero escuchar', 'ponme un video de'.",
      {"consulta": ("string", "Canción, artista o video copiado tal como lo dijo el usuario (título completo, "
                              "sin cambiar ni expandir abreviaturas), p.ej. 'Voy a llevarte pa PR Bad Bunny'"),
       "plataforma": ("string", "Solo si el usuario la nombra; si no, se usa lo que tenga abierto",
                      ["youtube", "youtube_music", "spotify"])},
      required=["consulta"])
def reproducir(consulta: str, plataforma: str = "") -> str:
    # "con el video de así ganó…", "la canción de X" -> "así ganó…", "X"
    consulta = re.sub(r"^\s*((pon|con|ponme|reproduce)\s+)?((el|la|un|una)\s+)?(video|canci[oó]n|rola|tema)"
                      r"(\s+(de|del|que se llama))?\s*", "", consulta, flags=re.I).strip() or consulta.strip()
    if _norm(consulta) in ([], ["cancion"], ["video"], ["musica"], ["algo"], ["la", "cancion"], ["el", "video"]):
        return "¿Qué canción o video quieres que ponga?"
    act = _active()
    if not plataforma:  # priorizar lo que ya está abierto
        if _is_proc(act, "spotify"):
            plataforma = "spotify"
        elif act and "music.youtube.com" in act.url:
            plataforma = "youtube_music"
        else:
            plataforma = "youtube"
    if plataforma == "spotify":
        # La app de Spotify no permite iniciar la reproducción sin su API; abrimos la búsqueda.
        _startfile(f"spotify:search:{urllib.parse.quote(consulta)}")
        return f"Busqué {consulta} en Spotify; dale play al primer resultado"
    tab = _youtube_tab()
    if plataforma == "youtube" and tab and tab.youtube_id and act and act.hwnd == tab.hwnd:
        # Está viendo YouTube: si lo pedido es uno de sus recomendados ("ponme qué hace
        # Escocia"), abrir ese en vez de buscar.
        item = _best_sidebar_match(_sidebar(tab), consulta)
        if item:
            return _open_sidebar_item(tab, item)
    found = _youtube_first(consulta)
    if found is None:
        return f"Error: no encontré {consulta} en YouTube"
    vid, titulo, canal = found
    host = "music.youtube.com" if plataforma == "youtube_music" else "www.youtube.com"
    url = f"https://{host}/watch?v={vid}"

    def play() -> str:
        tab = _youtube_tab()
        if tab:  # ya hay YouTube abierto: usar esa pestaña
            context.navigate(tab.hwnd, url)
        else:
            webbrowser.open(url)
        return f"Reproduciendo {titulo}"

    # Si el resultado no se parece a lo pedido, probablemente se entendió mal: preguntar.
    if _match(consulta, f"{titulo} {canal}") < 0.6:
        return ask(f"Encontré {titulo}, de {canal}. ¿Lo pongo?", play)
    return play()


def _new_video_title(tab: "context.Window") -> str:
    """Espera a que la pestaña cambie de video y devuelve su título ("" si no cambió)."""
    if LINUX:
        # En Wayland la URL se copia de la barra (intrusivo): se espera a que cambie el título
        # de la ventana y se lee una sola vez.
        antes = context.window_title(tab.hwnd)
        for _ in range(25):
            time.sleep(0.15)
            if context.window_title(tab.hwnd) != antes:
                break
        url = context.browser_url(tab.hwnd)
        if "watch?v=" not in url or f"v={tab.youtube_id}" in url:
            return ""
        try:
            return _http_json("https://www.youtube.com/oembed", {"url": url, "format": "json"})["title"]
        except Exception:
            return "el video"
    for _ in range(25):
        time.sleep(0.15)
        url = context.browser_url(tab.hwnd)
        if "watch?v=" in url and f"v={tab.youtube_id}" not in url:
            # El título de la pestaña tarda ~2 s en cambiar; oEmbed lo da al momento.
            try:
                return _http_json("https://www.youtube.com/oembed", {"url": url, "format": "json"})["title"]
            except Exception:
                return "el video"
    return ""


def _sidebar(tab: "context.Window") -> list:
    """Recomendados tal como los ve el usuario (OCR de la columna derecha)."""
    from . import screen
    act = _active()
    if not act or act.hwnd != tab.hwnd:
        context.focus(tab.hwnd)
        time.sleep(0.3)
    try:
        return screen.youtube_sidebar(tab.hwnd)
    except Exception:
        log.exception("No pude leer los recomendados de la pantalla")
        return []


def _open_sidebar_item(tab: "context.Window", item) -> str:
    from . import screen
    screen.click(item.x, item.y)
    titulo = _new_video_title(tab)
    return f"Puse {titulo or item.title}"


def _best_sidebar_match(items: list, consulta: str):
    if not items:
        return None
    score, item = max(((_match(consulta, i.title), i) for i in items), key=lambda p: p[0])
    return item if score >= 0.6 else None


@tool("Abre un video recomendado de YouTube: la lista de la derecha del video que el usuario está viendo. "
      "Para 'el primer recomendado', 'el segundo de la derecha', 'pon el siguiente video', "
      "'el recomendado de Escocia'. No la uses para 'siguiente canción' (eso es controlar_medios).",
      {"posicion": ("integer", "1 = el primero de la lista o 'el siguiente video'; 2 = el segundo, etc."),
       "titulo": ("string", "Palabras del título si el usuario describe cuál ('el de Escocia'); si no, vacío")},
      required=[])
def youtube_recomendado(posicion: int = 1, titulo: str = "") -> str:
    tab = _youtube_tab()
    if not tab or not tab.youtube_id:
        return "Error: no veo ningún video de YouTube abierto"
    pos = max(1, int(posicion or 1))
    items = _sidebar(tab)
    if titulo:
        item = _best_sidebar_match(items, titulo)
        return _open_sidebar_item(tab, item) if item else reproducir(titulo)
    if len(items) >= pos:
        return _open_sidebar_item(tab, items[pos - 1])
    # Sin OCR (ventana estrecha, recomendados debajo del video, etc.):
    if pos == 1:
        # Shift+N = "siguiente video" de YouTube, el primero de *tu* lista de recomendados.
        context.focus(tab.hwnd)
        _send("shift+n")
        titulo = _new_video_title(tab)
        if titulo:
            return f"Puse {titulo}"
    # Último recurso: la lista sin sesión iniciada (puede no coincidir con la del usuario).
    related = _youtube_related(tab.youtube_id)
    if len(related) < pos:
        return "Error: no encontré esa recomendación"
    vid, titulo = related[pos - 1]
    context.navigate(tab.hwnd, f"https://www.youtube.com/watch?v={vid}")
    return f"Puse {titulo}"


# ---------------------------------------------------------------- navegador

@tool("Abre una página web en el navegador predeterminado.",
      {"url": ("string", "Dirección completa o dominio, p.ej. youtube.com")})
def abrir_web(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url.strip()
    webbrowser.open(url)
    return f"Abrí {urllib.parse.urlparse(url).netloc.removeprefix('www.')}"


@tool("Busca algo en internet abriendo el navegador con los resultados.",
      {"consulta": ("string", "Lo que hay que buscar"),
       "sitio": ("string", "Dónde buscar", ["google", "youtube", "maps", "wikipedia"])},
      required=["consulta"])
def buscar_web(consulta: str, sitio: str = "google") -> str:
    q = urllib.parse.quote_plus(consulta)
    urls = {
        "google": f"https://www.google.com/search?q={q}",
        "youtube": f"https://www.youtube.com/results?search_query={q}",
        "maps": f"https://www.google.com/maps/search/{q}",
        "wikipedia": f"https://es.wikipedia.org/w/index.php?search={q}",
    }
    webbrowser.open(urls.get(sitio, urls["google"]))
    return f"Busqué '{consulta}' en {sitio}"


# ---------------------------------------------------------------- aplicaciones

_ALIASES = {
    "bloc de notas": "notepad", "notas": "notepad", "calculadora": "calc",
    "explorador": "explorer", "archivos": "explorer", "paint": "mspaint",
    "configuración": "ms-settings:", "ajustes": "ms-settings:",
    "terminal": "wt", "consola": "cmd", "administrador de tareas": "taskmgr",
    "calendario": "outlookcal:", "correo": "outlookmail:", "reloj": "ms-clock:",
    "cámara": "microsoft.windows.camera:", "tienda": "ms-windows-store:",
}


def _start_menu_shortcuts() -> dict[str, Path]:
    found = {}
    for root in (Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
                 Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs"):
        if root.exists():
            for p in root.rglob("*.lnk"):
                found.setdefault(p.stem.lower(), p)
    # El escritorio solo en su primer nivel: recorrerlo entero tardaba más de 5 s.
    for p in (Path.home() / "Desktop").glob("*.lnk"):
        found.setdefault(p.stem.lower(), p)
    return found


_LINUX_ALIASES = {
    "bloc de notas": "text editor", "notas": "text editor", "editor de texto": "text editor",
    "calculadora": "calculator", "explorador": "files", "archivos": "files", "explorador de archivos": "files",
    "navegador": "chromium", "configuración": "settings", "ajustes": "settings", "consola": "terminal",
    "administrador de tareas": "btop", "monitor del sistema": "btop", "correo": "mail",
}


def _linux_app(key: str) -> dict | None:
    from .linux import desktop
    apps = desktop.desktop_apps()
    key = _LINUX_ALIASES.get(key, key)
    if key in apps:
        return apps[key]
    partial = [k for k in apps if key in k and "uninstall" not in k]
    return apps[min(partial, key=len)] if partial else None


def _abrir_aplicacion_linux(nombre: str, key: str) -> str:
    from .linux import desktop
    app = _linux_app(key)
    if app is not None:
        desktop.launch(app)
        return f"Abrí {app['name']}"
    close = difflib.get_close_matches(key, desktop.desktop_apps(), n=1, cutoff=0.6)
    if close:
        app = desktop.desktop_apps()[close[0]]
        return ask(f"No tengo {nombre}, pero sí {app['name']}. ¿Lo abro?",
                   lambda: (desktop.launch(app), f"Abrí {app['name']}")[1])
    return f"Error: no encontré la aplicación {nombre}"


def _open_window(nombre: str) -> "context.Window | None":
    """Ventana ya abierta de ese programa, si la hay."""
    key = nombre.lower().strip()
    key = _PROCESS_ALIASES.get(key, key)
    for w in ([ctx.snapshot.activa] + ctx.snapshot.otras) if ctx.snapshot else []:
        if w and (key in w.app.lower() or key.replace(" ", "") in w.proceso):
            return w
    return None


@tool("Abre un programa o aplicación instalada (Chrome, Spotify, Word, calculadora, etc.). "
      "Si ya está abierto, lo trae al frente.",
      {"nombre": ("string", "Nombre del programa")})
def abrir_aplicacion(nombre: str) -> str:
    if EXTRA_OPEN:  # Omarchy: su terminal, navegador y web apps
        r = EXTRA_OPEN(nombre)
        if r is not None:
            return r
    key = nombre.lower().strip()
    abierta = _open_window(nombre)
    if abierta:  # ya está abierta: traerla al frente en vez de abrir otra
        context.focus(abierta.hwnd)
        return f"Ahí tienes {abierta.app}"
    key = re.sub(r"^(mi|el|la)\s+", "", key)
    if key in ("calendario", "agenda"):
        return _abrir_calendario()
    if LINUX:
        return _abrir_aplicacion_linux(nombre, key)
    if key in _ALIASES:
        try:
            os.startfile(_ALIASES[key])
            return f"Abrí {nombre}"
        except OSError:
            pass  # protocolo no registrado (p.ej. app no instalada): probar el menú Inicio
    shortcuts = _start_menu_shortcuts()
    match = shortcuts.get(key)
    if match is None:
        # coincidencia parcial ("chrome" -> "google chrome")
        partial = [k for k in shortcuts if key in k and "uninstall" not in k and "desinstalar" not in k]
        if partial:
            match = shortcuts[min(partial, key=len)]
    if match is not None:
        os.startfile(str(match))
        return f"Abrí {match.stem}"
    # Solo aproximada: puede ser otra app (o se entendió mal el nombre), así que preguntar.
    close = difflib.get_close_matches(key, shortcuts, n=1, cutoff=0.6)
    if close:
        lnk = shortcuts[close[0]]
        return ask(f"No tengo {nombre}, pero sí {lnk.stem}. ¿Lo abro?",
                   lambda: (os.startfile(str(lnk)), f"Abrí {lnk.stem}")[1])
    return f"Error: no encontré la aplicación {nombre}"


def _abrir_calendario() -> str:
    if ctx.config.calendario == "kalmiya":
        ctx.open_calendar()
        return "Ahí tienes tu calendario"
    if ctx.config.calendario != "google" and not LINUX:
        # Calendario de Windows/Outlook clásico, luego el Outlook nuevo (no registra outlookcal:).
        for uri, nombre in (("outlookcal:", "el calendario"), ("ms-outlook:", "Outlook")):
            try:
                os.startfile(uri)
                return f"Abrí {nombre}"
            except OSError:
                continue
    webbrowser.open("https://calendar.google.com")
    return "Abrí Google Calendar"


def _whatsapp_window() -> "context.Window | None":
    snap = context.capture(with_url=False)
    # Windows: la app (whatsapp.root.exe). Linux: la web app de Omarchy u otra ventana de WhatsApp Web.
    return next((w for w in [snap.activa, *snap.otras] if w and (w.proceso.startswith("whatsapp")
                 or (LINUX and "whatsapp" in w.proceso))), None)


def _open_whatsapp() -> None:
    if not LINUX:
        os.startfile("whatsapp:")
        return
    from .linux import desktop
    app = _linux_app("whatsapp")
    if app is not None:
        desktop.launch(app)
    elif shutil.which("omarchy-launch-webapp"):
        desktop.launch_cmd("omarchy-launch-webapp https://web.whatsapp.com/")
    else:
        raise OSError("WhatsApp no está instalado")


@tool("Abre la conversación de un contacto o grupo en la app de WhatsApp de la computadora "
      "('abre el chat de mamá', 'abre la conversación de Ana en WhatsApp').",
      {"contacto": ("string", "Nombre del contacto o grupo tal como lo dijo el usuario")})
def whatsapp_abrir_chat(contacto: str) -> str:
    w = _whatsapp_app()
    if isinstance(w, str):
        return w
    if [x for x in _norm(contacto) if x not in ("whatsapp", "app", "la", "el")] == []:
        return "Ahí tienes WhatsApp"  # "abre WhatsApp": solo la app
    nombre = _whatsapp_open_chat(w, contacto)
    return f"Abrí el chat de {nombre}" if nombre else f"No encontré a {contacto} en WhatsApp"


def _whatsapp_app() -> "context.Window | str":
    """Ventana de WhatsApp al frente (abriendo la app si hace falta), o un mensaje de error."""
    w = _whatsapp_window()
    if w is None:  # no está abierto: abrir la app (no la web)
        try:
            _open_whatsapp()
        except OSError:
            return "Error: no encontré la app de WhatsApp instalada"
        for _ in range(30):
            time.sleep(0.5)
            if (w := _whatsapp_window()) is not None:
                break
        else:
            return "Error: WhatsApp no terminó de abrir"
        time.sleep(1.5)
    context.focus(w.hwnd)
    return w


def _paste(texto: str) -> None:
    previo = _clip_get()
    _clip_set(texto)
    _send("ctrl+v")
    time.sleep(0.25)
    if previo is not None:
        _clip_set(previo)


def _whatsapp_open_chat(w: "context.Window", contacto: str) -> str | None:
    """Abre el chat y devuelve su nombre tal como aparece en WhatsApp (None si no está)."""
    from . import screen
    time.sleep(0.3)

    def best(lines):
        scored = [(_match(contacto, ln.text), -len(ln.text), ln) for ln in lines]
        scored = [s for s in scored if s[0] >= 0.6]
        return max(scored, key=lambda s: s[:2])[2] if scored else None

    nombres, buscar = screen.whatsapp_chats(w.hwnd)
    chat = best(nombres)
    if chat is None and buscar is not None:
        # No está entre los visibles: escribirlo en el buscador de WhatsApp.
        screen.click(buscar.x + 40, buscar.y + buscar.h // 2)
        time.sleep(0.2)
        _send("ctrl+a")
        _paste(contacto)
        time.sleep(1.0)
        nombres, _ = screen.whatsapp_chats(w.hwnd)
        chat = best([n for n in nombres if n.y > buscar.y + 30])
        if chat is None:
            _send("esc")
    if chat is None:
        return None
    screen.click(chat.x + 40, chat.y + chat.h // 2)
    return chat.text


def _whatsapp_header_is(w: "context.Window", nombre: str) -> bool:
    """¿El chat abierto (cabecera de la derecha) es el de `nombre`?"""
    from . import screen
    with screen._physical_pixels():
        top = _rect(w.hwnd)[1]
    cabecera = [ln.text for ln in screen.window_lines(w.hwnd, 0.42, 0.85) if ln.y < top + 180]
    return any(_match(nombre, t) >= 0.8 for t in cabecera)


def _whatsapp_focus_compose(w: "context.Window") -> None:
    """Clic en el cuadro "Escribe un mensaje" del chat abierto."""
    from . import screen
    lines = screen.window_lines(w.hwnd, 0.42, 1.0)
    caja = next((ln for ln in lines if ln.text.lower().startswith("escribe un mensaje")), None)
    if caja:
        screen.click(caja.x + 30, caja.y + caja.h // 2)
    else:  # ya hay un borrador escrito: el cuadro está abajo, al centro del panel derecho
        with screen._physical_pixels():
            l, t, r, b = _rect(w.hwnd)
        screen.click(l + int((r - l) * 0.65), b - 75)
    time.sleep(0.15)


@tool("Escribe un mensaje de WhatsApp a un contacto o grupo y pregunta antes de enviarlo "
      "('mándale a mamá que ya voy', 'escríbele a Ana que mañana le llamo').",
      {"contacto": ("string", "Nombre del contacto o grupo tal como lo dijo el usuario"),
       "mensaje": ("string", "El mensaje exactamente con las palabras que dictó el usuario, sin cambiarlo ni resumirlo")})
def whatsapp_mensaje(contacto: str, mensaje: str) -> str:
    mensaje = mensaje.strip()
    if not mensaje:
        return f"¿Qué le escribo a {contacto}?"
    mensaje = mensaje[0].upper() + mensaje[1:]
    w = _whatsapp_app()
    if isinstance(w, str):
        return w
    nombre = _whatsapp_open_chat(w, contacto)
    if not nombre:
        return f"No encontré a {contacto} en WhatsApp"
    time.sleep(0.6)
    _whatsapp_focus_compose(w)
    _send("ctrl+a")  # sustituir cualquier borrador anterior
    _paste(mensaje)

    def enviar() -> str:
        # Comprobar que seguimos en el chat correcto antes de pulsar Enter.
        context.focus(w.hwnd)
        if not _whatsapp_header_is(w, nombre):
            return f"No lo envié: ya no está abierto el chat de {nombre}"
        _whatsapp_focus_compose(w)
        _send("end")
        _send("enter")
        return f"Enviado a {nombre}"

    def borrar() -> str:
        context.focus(w.hwnd)
        if _whatsapp_header_is(w, nombre):
            _whatsapp_focus_compose(w)
            _send("ctrl+a")
            _send("delete")
        return "Vale, no lo envío"

    # Enviar un mensaje en tu nombre siempre pide confirmación (aunque esté desactivado "preguntar si dudo").
    return ask(f"Le escribí a {nombre}: {mensaje.rstrip('.')}. ¿Lo envío?", enviar, siempre=True,
               al_cancelar=borrar)


_PROCESS_ALIASES = {"calculadora": "calculatorapp", "bloc de notas": "notepad", "navegador": "chrome",
                     "google chrome": "chrome", "edge": "msedge", "word": "winword", "explorador": "explorer"}


@tool("Cierra un programa abierto (Spotify, Chrome, la calculadora, etc.).",
      {"nombre": ("string", "Nombre del programa")})
def cerrar_aplicacion(nombre: str) -> str:
    if LINUX:
        return _cerrar_aplicacion_linux(nombre)
    key = nombre.lower().strip()
    key = _PROCESS_ALIASES.get(key, key).replace(" ", "")
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW).stdout
    procs = {line.split('","')[0].strip('"') for line in out.splitlines() if line}
    match = [p for p in procs if key in p.lower().removesuffix(".exe")]
    seguro = bool(match)
    if not match:
        close = difflib.get_close_matches(key, [p.lower().removesuffix(".exe") for p in procs], n=1, cutoff=0.7)
        match = [p for p in procs if close and p.lower().removesuffix(".exe") == close[0]]
    if not match or key == "explorer":
        return f"Error: no encontré {nombre} abierto"

    def close() -> str:
        for p in match:  # cierre normal (como pulsar la X); sin /F para no perder trabajo sin guardar
            subprocess.run(["taskkill", "/IM", p], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return f"Cerré {nombre}"

    if not seguro:
        return ask(f"No veo {nombre}, pero sí {match[0].removesuffix('.exe')}. ¿Lo cierro?", close)
    return close()


def _cerrar_aplicacion_linux(nombre: str) -> str:
    """Cierra sus ventanas como al pulsar la X (Hyprland); si no tiene ventana, termina el proceso."""
    import psutil

    from .linux import desktop
    key = nombre.lower().strip()
    key = _LINUX_ALIASES.get(key, key)
    wins = desktop.clients()

    def names(c):
        cls = (c.get("class") or "").lower()
        return {cls, context._friendly_name(cls, c.get("title", "")).lower()}
    match = [c for c in wins if any(key in n or key.replace(" ", "") in n for n in names(c))]
    seguro = bool(match)
    if not match:
        todos = {n: c for c in wins for n in names(c)}
        close = difflib.get_close_matches(key, todos, n=1, cutoff=0.7)
        match = [todos[close[0]]] if close else []
    if not match:
        procs = [p for p in psutil.process_iter(["name", "username"])
                 if p.info["username"] == psutil.Process().username() and key.replace(" ", "") in (p.info["name"] or "").lower()]
        if procs:
            for p in procs:
                p.terminate()
            return f"Cerré {nombre}"
        return f"Error: no encontré {nombre} abierto"

    def close() -> str:
        for c in match:
            desktop.close_window(c["address"])
        return f"Cerré {nombre}"

    if not seguro:
        cls = (match[0].get("class") or "").lower()
        return ask(f"No veo {nombre}, pero sí {context._friendly_name(cls, match[0].get('title', ''))}. ¿Lo cierro?",
                   close)
    return close()


@tool("Cierra o cambia a una pestaña del navegador nombrándola, aunque no sea la que está a la vista: "
      "'cierra la pestaña de YouTube', 'cierra la de Xataka', 've a la pestaña de WhatsApp Web'.",
      {"accion": ("string", "Qué hacer", ["cerrar", "cambiar"]),
       "nombre": ("string", "Cómo la nombró el usuario: sitio o parte del título, p.ej. 'YouTube', 'Xataka'")})
def pestana(accion: str, nombre: str) -> str:
    nombre = re.sub(r"^(la |el )?(pesta[ñn]a )?(de |del |que dice |donde )?", "", nombre.strip(), flags=re.I)

    def score(titulo: str) -> float:
        return 1.0 if nombre.lower() in titulo.lower() else _match(nombre, titulo)
    found = context.find_tab(score)
    if found is None:
        return "Error: no veo ningún navegador abierto"
    hwnd, titulo, s = found
    if LINUX and s < 0.5:
        # En Wayland solo se ven las pestañas al frente; las demás, con el buscador de pestañas.
        cerrar = accion.startswith("cerr")
        if context.tab_search(nombre, "cerrar" if cerrar else "cambiar") is None:
            return "Error: no veo ningún navegador abierto"
        return f"Cerré la pestaña de {nombre}" if cerrar else f"Ahí tienes {nombre}"
    corto = context.clean_title(titulo, False)[:60]
    if s < 0.5:
        return f"No encontré una pestaña de {nombre}"
    if not context.tab_action(hwnd, titulo, "cerrar" if accion.startswith("cerr") else "cambiar"):
        return f"No pude {'cerrar' if accion.startswith('cerr') else 'abrir'} esa pestaña"
    return f"Cerré la pestaña {corto}" if accion.startswith("cerr") else f"Ahí tienes {corto}"


def tab_intent(user_text: str) -> tuple[str, dict] | None:
    """"Cierra la pestaña de X" es inequívoco: sin el modelo (antes cerraba la pestaña a la vista)."""
    t = " ".join(_norm(user_text))
    # "Cierra esta pestaña": la activa (el modelo a veces elegía cerrar la ventana entera).
    if re.match(r"^(cierra|cierrame|cerrar|quita)\s+(esta|la|la actual|esta)\s*(pesta[nñ]a|tab)?\s*(actual)?$", t):
        return "controlar_ventana", {"accion": "cerrar_pestana"}
    m = re.match(r"^(cierra|cierrame|cerrar|quita)\s+(la\s+)?(pesta[nñ]a|tab)\s+(de|del|que dice|donde esta)\s+(?P<n>.+)$", t)
    if m:
        return "pestana", {"accion": "cerrar", "nombre": m.group("n")}
    # "Cierra la de los drones" (sin decir pestaña): solo si de verdad hay una pestaña así.
    m = re.match(r"^(cierra|cierrame|quita)\s+la\s+(de|del|que dice|donde esta)\s+(?P<n>.+)$", t)
    if m:
        n = re.sub(r"^(las?\s+|los?\s+)?(noticias?\s+(de|del|sobre)\s+)?", "", m.group("n"))
        try:
            found = context.find_tab(lambda ti: _match(n, ti))
        except Exception:
            found = None
        if found and found[2] >= 0.5:
            return "pestana", {"accion": "cerrar", "nombre": n}
    m = re.match(r"^(ve|vete|cambia|cambiate|pasa|pasate|abre|ponme)\s+(a\s+)?(la\s+)?(pesta[nñ]a|tab)\s+"
                 r"(de|del|que dice|donde esta)\s+(?P<n>.+)$", t)
    if m:
        return "pestana", {"accion": "cambiar", "nombre": m.group("n")}
    return None


@tool("Actúa sobre la ventana o pestaña que el usuario tiene a la vista ('esto', 'esta pestaña'): "
      "cerrar la pestaña, volver atrás, recargar, pantalla completa, minimizar, maximizar o cerrar la ventana. "
      "Si nombra otra pestaña ('cierra la de YouTube'), usa pestana.",
      {"accion": ("string", "Acción", ["cerrar_pestana", "atras", "adelante", "recargar", "pantalla_completa",
                                       "minimizar", "maximizar", "cerrar_ventana"])})
def controlar_ventana(accion: str) -> str:
    w = _active()
    accion = accion.replace("ñ", "n")
    if accion in ("cerrar_pestana", "atras", "adelante", "recargar") and not (w and w.es_navegador):
        # Acciones de pestaña con otra app al frente: van al navegador más reciente, no a esa app.
        w = ctx.snapshot.browser() if ctx.snapshot else None
    if w is None:
        return "Error: no hay ninguna ventana activa"
    if LINUX and accion in ("minimizar", "cerrar_ventana", "maximizar"):
        from .linux import desktop
        {"minimizar": desktop.minimize, "cerrar_ventana": desktop.close_window,
         "maximizar": desktop.fullscreen}[accion](w.hwnd)
        return {"minimizar": "Minimicé", "cerrar_ventana": "Cerré", "maximizar": "Maximicé"}[accion] + f" {w.app}"
    if accion in ("minimizar", "cerrar_ventana", "maximizar"):
        import win32con
        import win32gui
    if accion == "minimizar":
        win32gui.ShowWindow(w.hwnd, win32con.SW_MINIMIZE)
        return f"Minimicé {w.app}"
    if accion == "cerrar_ventana":
        win32gui.PostMessage(w.hwnd, win32con.WM_CLOSE, 0, 0)
        return f"Cerré {w.app}"
    context.focus(w.hwnd)
    if accion == "maximizar":
        win32gui.ShowWindow(w.hwnd, win32con.SW_MAXIMIZE)
        return f"Maximicé {w.app}"
    teclas = {"cerrar_pestana": "ctrl+w", "atras": "alt+left", "adelante": "alt+right", "recargar": "f5",
              # En un video de YouTube, "f" pone el reproductor en pantalla completa.
              "pantalla_completa": "f" if w.youtube_id else "f11"}
    if accion not in teclas:
        return f"Error: acción desconocida {accion}"
    _send(teclas[accion])
    return {"cerrar_pestana": "Cerré la pestaña", "atras": "Regresé", "adelante": "Avancé",
            "recargar": "Recargué", "pantalla_completa": "Pantalla completa"}[accion]


_FOLDERS = {
    "descargas": "Downloads", "documentos": "Documents", "escritorio": "Desktop",
    "imágenes": "Pictures", "imagenes": "Pictures", "fotos": "Pictures",
    "videos": "Videos", "música": "Music", "musica": "Music",
}


def _user_dir(name: str) -> Path:
    """Carpeta del usuario; en Linux, la de xdg-user-dirs (p. ej. ~/Descargas en un sistema en español)."""
    xdg = {"Downloads": "DOWNLOAD", "Documents": "DOCUMENTS", "Desktop": "DESKTOP", "Pictures": "PICTURES",
           "Videos": "VIDEOS", "Music": "MUSIC"}.get(name)
    if LINUX and xdg:
        try:
            out = subprocess.run(["xdg-user-dir", xdg], capture_output=True, text=True, timeout=3).stdout.strip()
            if out and Path(out).exists() and Path(out) != Path.home():
                return Path(out)
        except Exception:
            pass
    return Path.home() / name


@tool("Abre una carpeta del usuario en el administrador de archivos.",
      {"carpeta": ("string", "Carpeta", ["descargas", "documentos", "escritorio", "imagenes", "videos", "musica"])})
def abrir_carpeta(carpeta: str) -> str:
    path = _user_dir(_FOLDERS.get(carpeta.lower(), carpeta))
    if not path.exists():
        return f"No existe la carpeta {path}"
    _startfile(str(path))
    return f"Abrí la carpeta {carpeta}"


# ---------------------------------------------------------------- calendario

_DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def _parse_date(fecha: str) -> dt.date:
    """AAAA-MM-DD o como lo dijo el usuario ("viernes", "mañana"): el modelo pequeño
    se equivoca al calcular días de la semana, así que lo resolvemos aquí."""
    hoy = dt.date.today()
    words = _norm(fecha)
    text = " ".join(words)
    if "pasado manana" in text:
        return hoy + dt.timedelta(days=2)
    if "manana" in words:
        return hoy + dt.timedelta(days=1)
    if "hoy" in words:
        return hoy
    for i, d in enumerate(_DIAS):
        if d in words:  # el próximo (si hoy es viernes, "el viernes" es el de la semana que viene)
            return hoy + dt.timedelta(days=(i - hoy.weekday() - 1) % 7 + 1)
    return dt.date.fromisoformat(fecha.strip())


def _parse_dt(fecha: str, hora: str) -> dt.datetime:
    day = _parse_date(fecha)
    h = (hora or "09:00").strip()
    parts = h.replace(".", ":").split(":")
    return dt.datetime.combine(day, dt.time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0))


def _outlook_event(titulo, inicio, minutos, descripcion, lugar) -> bool:
    # El Outlook "nuevo" se registra como Outlook.Application pero no implementa CreateItem;
    # ante cualquier fallo se usa el .ics.
    try:
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application")
        item = outlook.CreateItem(1)  # olAppointmentItem
        item.Subject = titulo
        item.Start = inicio.strftime("%Y-%m-%d %H:%M")
        item.Duration = minutos
        item.Body = descripcion or ""
        item.Location = lugar or ""
        item.ReminderSet = True
        item.ReminderMinutesBeforeStart = 15
        item.Save()
        return True
    except Exception as e:
        log.info("Outlook clásico no disponible (%s); uso .ics", e)
        return False


def _ics_event(titulo, inicio, minutos, descripcion, lugar) -> Path:
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    fin = inicio + dt.timedelta(minutes=minutos)
    f = "%Y%m%dT%H%M%S"
    esc = lambda s: (s or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")
    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Kalmiya//ES",
        "BEGIN:VEVENT", f"UID:{uuid.uuid4()}@kalmiya",
        f"DTSTAMP:{dt.datetime.now(dt.timezone.utc).strftime(f)}Z",
        f"DTSTART:{inicio.strftime(f)}", f"DTEND:{fin.strftime(f)}",
        f"SUMMARY:{esc(titulo)}", f"DESCRIPTION:{esc(descripcion)}", f"LOCATION:{esc(lugar)}",
        "BEGIN:VALARM", "TRIGGER:-PT15M", "ACTION:DISPLAY", f"DESCRIPTION:{esc(titulo)}", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR", "",
    ])
    safe = "".join(c for c in titulo if c.isalnum() or c in " -_")[:40] or "evento"
    path = EVENTS_DIR / f"{inicio:%Y-%m-%d_%H%M} {safe}.ics"
    path.write_bytes(ics.encode("utf-8"))  # sin traducir \r\n
    return path


def _google_event(titulo, inicio, minutos, descripcion, lugar) -> None:
    fin = inicio + dt.timedelta(minutes=minutos)
    f = "%Y%m%dT%H%M%S"
    q = urllib.parse.urlencode({
        "action": "TEMPLATE", "text": titulo, "dates": f"{inicio.strftime(f)}/{fin.strftime(f)}",
        "details": descripcion or "", "location": lugar or "",
    })
    webbrowser.open(f"https://calendar.google.com/calendar/render?{q}")


@tool("Crea un evento o cita en el calendario.",
      {"titulo": ("string", "Título del evento"),
       "fecha": ("string", "El día tal como lo dijo el usuario ('viernes', 'mañana', 'hoy') o AAAA-MM-DD"),
       "hora": ("string", "Hora de inicio HH:MM en 24 h"),
       "duracion_minutos": ("integer", "Duración en minutos"),
       "descripcion": ("string", "Notas opcionales"),
       "lugar": ("string", "Lugar opcional")},
      required=["titulo", "fecha"])
def crear_evento(titulo: str, fecha: str, hora: str = "09:00", duracion_minutos: int = 60,
                 descripcion: str = "", lugar: str = "") -> str:
    from .calendar_store import spoken_date, spoken_time
    inicio = _parse_dt(fecha, hora)
    minutos = int(duracion_minutos or 60)
    modo = ctx.config.calendario
    cuando = f"{spoken_date(inicio.date())} a {spoken_time(inicio)}"
    if modo == "outlook" and _outlook_event(titulo, inicio, minutos, descripcion, lugar):
        return f"Guardé {titulo} en Outlook para {cuando}"
    if modo == "ics":
        _startfile(str(_ics_event(titulo, inicio, minutos, descripcion, lugar)))
        return f"Creé {titulo} para {cuando} y lo abrí en tu app de calendario"
    ctx.calendar.add(titulo, inicio, minutos, lugar, descripcion)
    destino = " y en Google Calendar" if ctx.calendar.google_connected else ""
    return f"Agendé {titulo} para {cuando}{destino}"


@tool("Dice qué eventos tiene el usuario en su calendario un día ('¿qué tengo mañana?', '¿tengo algo el viernes?').",
      {"fecha": ("string", "El día tal como lo dijo el usuario ('hoy', 'mañana', 'viernes') o AAAA-MM-DD")},
      required=[])
def ver_agenda(fecha: str = "hoy") -> str:
    return ctx.calendar.describe_day(_parse_date(fecha or "hoy"))


# ---------------------------------------------------------------- videos

def _ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _download(objetivo: str, solo_audio: bool) -> None:
    import yt_dlp
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg()
    opts = {
        "outtmpl": str(DOWNLOADS_DIR / "%(title).80s.%(ext)s"),
        "noplaylist": True, "quiet": True, "no_warnings": True,
    }
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    if solo_audio:
        opts["format"] = "bestaudio/best"
        if ffmpeg:
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]
    else:
        opts["format"] = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b" if ffmpeg else "b[ext=mp4]/b"
        opts["merge_output_format"] = "mp4"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(objetivo, download=True)
            if info and "entries" in info:
                info = info["entries"][0]
            title = (info or {}).get("title", "el video")
        ctx.notify(f"Descarga lista: {title}")
        ctx.speak(f"Terminé de descargar {title}")
        _startfile(str(DOWNLOADS_DIR))
    except Exception as e:
        log.exception("Descarga fallida")
        ctx.notify(f"Error al descargar: {e}")
        ctx.speak("No pude descargar el video")


_DEICTICOS = {"", "este", "este video", "esto", "esta", "esta cancion", "esta canción", "el video", "actual",
              "la cancion", "la canción", "este video de youtube"}


@tool("Descarga un video (o solo su audio) de YouTube u otro sitio. Acepta un enlace o una búsqueda; "
      "sin nada (o 'este video') descarga el que el usuario tiene abierto.",
      {"url_o_busqueda": ("string", "Enlace o texto para buscarlo; vacío = el video abierto"),
       "solo_audio": ("boolean", "true para guardar solo el audio en MP3")},
      required=[])
def descargar_video(url_o_busqueda: str = "", solo_audio: bool = False) -> str:
    objetivo = url_o_busqueda.strip()
    if objetivo.lower() in _DEICTICOS:
        b = ctx.snapshot.browser() if ctx.snapshot else None
        if not b or not b.url.startswith("http"):
            return "Error: no veo ningún video abierto. ¿Cuál quieres descargar?"
        objetivo = b.url
    if not objetivo.startswith(("http://", "https://")):
        objetivo = f"ytsearch1:{objetivo}"
    threading.Thread(target=_download, args=(objetivo, bool(solo_audio)), daemon=True).start()
    return "Empecé la descarga; te aviso cuando termine"


# ---------------------------------------------------------------- sistema

def _system_volume():
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    return AudioUtilities.GetSpeakers().EndpointVolume


@tool("Controla la reproducción de música o video que ya está sonando (pausar, siguiente/anterior canción) "
      "y el volumen del sistema. Para un porcentaje exacto ('al 50 por ciento') usa fijar_volumen con nivel.",
      {"accion": ("string", "Acción", ["pausar_reanudar", "siguiente", "anterior", "subir_volumen", "bajar_volumen",
                                       "silenciar", "fijar_volumen"]),
       "nivel": ("integer", "Solo con fijar_volumen: porcentaje de 0 a 100")},
      required=["accion"])
def controlar_medios(accion: str, nivel: int | None = None) -> str:
    if nivel is not None and accion in ("subir_volumen", "bajar_volumen"):
        accion = "fijar_volumen"  # "baja el volumen al 50 %": el modelo a veces elige bajar_volumen
    if accion == "fijar_volumen":
        if nivel is None:
            return "Error: ¿a qué porcentaje lo pongo?"
        nivel = max(0, min(100, int(nivel)))
        if LINUX:
            from .linux import desktop
            desktop.set_volume(nivel)
            return f"Volumen al {nivel} por ciento"
        ev = _system_volume()
        ev.SetMute(0, None)
        ev.SetMasterVolumeLevelScalar(nivel / 100, None)
        return f"Volumen al {nivel} por ciento"
    teclas = {
        "pausar_reanudar": ("play/pause media", 1), "siguiente": ("next track", 1),
        "anterior": ("previous track", 1), "subir_volumen": ("volume up", 5),
        "bajar_volumen": ("volume down", 5), "silenciar": ("volume mute", 1),
    }
    key, veces = teclas.get(accion, (None, 0))
    if key is None:
        return f"Acción desconocida: {accion}"
    if LINUX:  # MPRIS para el reproductor (YouTube en el navegador, Spotify…) y wpctl para el volumen
        from .linux import desktop
        if accion in ("pausar_reanudar", "siguiente", "anterior"):
            if not desktop.media({"pausar_reanudar": "PlayPause", "siguiente": "Next", "anterior": "Previous"}[accion]):
                return "Error: no hay nada sonando"
        elif accion == "silenciar":
            desktop.toggle_mute()
        else:
            desktop.change_volume(10 if accion == "subir_volumen" else -10)
        veces = 0
    for _ in range(veces):
        _send(key)
        time.sleep(0.02)
    return {"pausar_reanudar": "Listo", "siguiente": "Siguiente", "anterior": "Anterior",
            "subir_volumen": "Subí el volumen", "bajar_volumen": "Bajé el volumen", "silenciar": "Silenciado"}[accion]


# No se expone "escribir texto" al modelo: LFM3-1.7B la usaba para contestar preguntas,
# tecleando en la ventana activa. Para escribir está el atajo de dictado.
def paste_text(texto: str) -> None:
    """Pega texto vía portapapeles (más rápido y fiable con acentos) y restaura el anterior."""
    if LINUX:
        # wtype escribe directamente con el teclado virtual: vale también en terminales,
        # donde Ctrl+V no pega.
        from .linux import desktop
        desktop.type_text(texto)
        return
    previo = _clip_get()
    _clip_set(texto)
    time.sleep(0.05)
    _send("ctrl+v")
    time.sleep(0.15)
    if previo is not None:
        threading.Timer(0.5, lambda: _clip_set(previo)).start()


@tool("Toma una captura de pantalla y la guarda en Imágenes.")
def captura_pantalla() -> str:
    folder = _user_dir("Pictures") / "Kalmiya"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"captura_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
    if LINUX:
        from .linux import desktop
        desktop.grab().save(path)
    else:
        from PIL import ImageGrab
        ImageGrab.grab(all_screens=True).save(path)
    _startfile(str(path))
    return "Tomé una captura de pantalla"


@tool("Programa un recordatorio hablado: dentro de N minutos ('en 20 minutos', 'en 2 horas' = 120) "
      "o un día y hora ('mañana a las 9'). Se guarda en el calendario.",
      {"mensaje": ("string", "Qué recordar, sin 'recuérdame', p.ej. 'sacar la ropa'"),
       "minutos": ("number", "Minutos desde ahora (solo si dijo 'en X minutos/horas')"),
       "fecha": ("string", "Día tal como lo dijo ('mañana', 'viernes', 'hoy') o AAAA-MM-DD"),
       "hora": ("string", "Hora HH:MM en 24 h")},
      required=["mensaje"])
def recordatorio(mensaje: str, minutos: float = 0, fecha: str = "", hora: str = "") -> str:
    from .calendar_store import spoken_date, spoken_time
    mensaje = re.sub(r"^(recu[ée]rdame|que|de)\s+", "", mensaje.strip(), flags=re.I).strip() or mensaje
    if minutos and float(minutos) > 0:
        cuando = dt.datetime.now() + dt.timedelta(minutes=float(minutos))
        ctx.calendar.add_reminder(mensaje, cuando)
        m = float(minutos)
        lapso = (f"{m / 60:g} {'hora' if m == 60 else 'horas'}" if m >= 60 and m % 60 == 0
                 else f"{m:g} {'minuto' if m == 1 else 'minutos'}")
        return f"Te recordaré {mensaje} en {lapso}"
    if not (fecha or hora):
        return "¿Cuándo te lo recuerdo?"
    cuando = _parse_dt(fecha or "hoy", hora or "09:00")
    if cuando < dt.datetime.now():
        if not fecha:  # "a las 8" cuando ya pasaron las 8: mañana
            cuando += dt.timedelta(days=1)
        else:
            return "Error: esa hora ya pasó. ¿Para cuándo lo quieres?"
    ctx.calendar.add_reminder(mensaje, cuando)
    return f"Te recordaré {mensaje} {spoken_date(cuando.date())} a {spoken_time(cuando)}"


@tool("Dice los recordatorios pendientes del usuario ('¿qué recordatorios tengo?').")
def ver_recordatorios() -> str:
    return ctx.calendar.describe_reminders()


@tool("Abre en pantalla la lista de recordatorios ('abre mis recordatorios').")
def abrir_recordatorios() -> str:
    ctx.open_reminders()
    return "Ahí tienes tus recordatorios"


# ---------------------------------------------------------------- noticias

class _News:
    items: list = []  # titulares de la última consulta, en el orden en que se leen (con su enlace real)
    pos: int = 0  # cuántos se han leído ya
    when: float = 0.0  # cuándo se leyeron por última vez


def news_intent(user_text: str) -> tuple[str, dict] | None:
    """Órdenes de noticias resueltas sin el modelo (con historial se confundía: "¿qué dice Xataka?"
    acababa buscando en YouTube y "abre la de las aspiradoras", en Google)."""
    t = " ".join(_norm(user_text))
    recent = _News.pos and time.time() - _News.when < 15 * 60
    if recent:
        m = re.match(r"^(abre|abreme|abrir|ensename|muestrame)\s+(la\s+)?(noticia\s+)?"
                     r"(?P<ord>primera|segunda|tercera|ultima)?\s*(de|del|sobre|que habla de|donde)?\s*(?P<q>.*)$", t)
        if m:
            if m.group("ord") and not m.group("q"):
                return "abrir_noticia", {"posicion": _NEWS_ORD[m.group("ord")]}
            q = m.group("q")
            # Sin "noticia", solo si se parece a un titular leído ("abre la calculadora" no lo es).
            if q and ("noticia" in t or max(_match(q, f"{i.titulo} {i.resumen}") for i in _News.items) >= 0.6):
                return "abrir_noticia", {"consulta": q}
    if re.search(r"\b(abre|abreme)\b.*\bnoticia", t):
        return "abrir_noticia", {"consulta": re.sub(r"^.*?\bnoticias?\s*(de|del|sobre)?\s*", "", t)}
    m = re.search(r"\bque (dice|cuenta|hay en|publica)\s+(el |la )?(?P<f>.+)$", t)
    if m:
        f = next((f["nombre"] for f in ctx.config.noticias or [] if _match(m.group("f"), f["nombre"]) >= 0.5), None)
        if f:
            return "resumen_noticias", {"fuente": f}
    if re.search(r"\b(noticias|titulares|encabezados)\b|que hay de nuevo|que paso hoy en el mundo", t) \
            and not re.search(r"\b(abre|busca|youtube)\b", t):
        m = re.search(r"\b(noticias|nuevo)\s+(de|sobre|en)\s+(?P<tema>.+)$", t)
        tema = m.group("tema") if m else ""
        return "resumen_noticias", {"tema": tema} if tema else {}
    return None


NEWS_BATCH = 3


def _interleave(items: list) -> list:
    """Alterna periódicos (uno de cada uno) para que los primeros tres no sean del mismo."""
    by_src: dict[str, list] = {}
    for i in items:
        by_src.setdefault(i.fuente, []).append(i)
    out, queues = [], list(by_src.values())
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


def _next_news() -> str:
    lote = _News.items[_News.pos:_News.pos + NEWS_BATCH]
    if not lote:
        return "Ya no tengo más titulares por ahora"
    _News.pos += len(lote)
    _News.when = time.time()
    texto = " ".join(f"De {i.fuente}: {i.titulo.rstrip('.')}." for i in lote)
    if _News.pos >= len(_News.items):
        return texto + " Esas son todas por ahora."
    return ask(texto + " ¿Quieres saber más?", _next_news, siempre=True, al_cancelar=lambda: "Vale.")


@tool("Lee los titulares de los periódicos del usuario (RSS), de tres en tres: "
      "'¿qué hay de nuevo?', 'resúmeme las noticias', 'noticias de tecnología', 'qué dice El Universal'.",
      {"fuente": ("string", "Periódico concreto si lo nombró; vacío para todos"),
       "tema": ("string", "Tema si lo dijo (p.ej. 'deportes', 'tecnología'); vacío si no")},
      required=[])
def resumen_noticias(fuente: str = "", tema: str = "") -> str:
    from . import news
    feeds = list(ctx.config.noticias or [])
    if not feeds:
        return "No tienes periódicos conectados. Añádelos en Configuración, en Noticias; ahí hay recomendados"
    if fuente:
        elegidos = [f for f in feeds if _match(fuente, f["nombre"]) >= 0.5]
        if not elegidos:
            nombres = ", ".join(f["nombre"] for f in feeds)
            return f"No tengo {fuente} entre tus periódicos. Tienes: {nombres}"
        feeds = elegidos
    items = news.headlines(feeds, per_feed=15 if (tema or fuente) else 6)
    if tema:
        temas = [w for w in _norm(tema) if len(w) > 3]
        filtrados = [i for i in items if any(w[:5] in " ".join(_norm(i.titulo + " " + i.resumen)) for w in temas)]
        items = filtrados or items
    if not items:
        return "No pude leer los periódicos ahora mismo. ¿Tienes internet?"
    _News.items, _News.pos = _interleave(items), 0
    return _next_news()


_NEWS_ORD = {"primera": 1, "primer": 1, "primero": 1, "segunda": 2, "segundo": 2, "tercera": 3, "tercero": 3,
              "ultima": -1, "ultimo": -1}


@tool("Abre en el navegador una noticia que Kalmiya acaba de leer ('abre la noticia de los drones', "
      "'abre la segunda'). Usa siempre esta herramienta para noticias, nunca abrir_web.",
      {"consulta": ("string", "De qué trata la noticia, con palabras del titular; vacío si dijo su posición"),
       "posicion": ("integer", "1, 2 o 3 si dijo 'la primera/segunda/tercera' de las que acaba de leer")},
      required=[])
def abrir_noticia(consulta: str = "", posicion: int = 0) -> str:
    leidas = _News.items[:_News.pos]
    if not leidas:
        return "Aún no te he leído noticias. Pídeme «resúmeme las noticias»"
    lote = leidas[-((len(leidas) - 1) % NEWS_BATCH + 1):]  # las últimas que dijo en voz alta
    elegida = None
    words = _norm(consulta)
    pos = int(posicion or 0) or next((_NEWS_ORD[w] for w in words if w in _NEWS_ORD), 0)
    if pos == -1:
        elegida = lote[-1]
    elif pos:
        elegida = lote[pos - 1] if pos <= len(lote) else None
    if elegida is None and consulta:
        # Primero entre las leídas; si no, entre todas las descargadas.
        for grupo in (leidas, _News.items):
            score, best = max(((_match(consulta, f"{i.titulo} {i.resumen}"), i) for i in grupo), key=lambda x: x[0])
            if score >= 0.5:
                elegida = best
                break
    if elegida is None and len(lote) == 1:
        elegida = lote[0]
    if elegida is None:
        return "¿Cuál de las noticias? Dime de qué trata o si es la primera, segunda o tercera"
    if not elegida.link.startswith("http"):
        return f"Esa noticia de {elegida.fuente} no trae enlace"
    webbrowser.open(elegida.link)
    return f"Abrí la noticia de {elegida.fuente}: {elegida.titulo}"


@tool("Bloquea la sesión (pantalla de bloqueo).")
def bloquear_pc() -> str:
    if LINUX:
        from .linux import desktop
        desktop.lock_screen()
        return "PC bloqueada"
    import ctypes
    ctypes.windll.user32.LockWorkStation()
    return "PC bloqueada"


# ---------------------------------------------------------------- despacho

_ORDINALES = {"primer", "primero", "primera", "segundo", "segunda", "tercer", "tercero", "tercera", "cuarto",
              "cuarta", "quinto", "quinta", "ultimo", "ultima", "siguiente", "proximo", "1", "2", "3", "4", "5"}
_NO_TITULO = {"pon", "ponme", "abre", "abreme", "reproduce", "dale", "al", "a", "el", "la", "los", "las", "un", "una",
              "de", "del", "que", "esta", "este", "video", "videos", "opcion", "recomendado", "recomendados",
              "recomendada", "sugerido", "derecha", "lado", "youtube", "otro", "en", "por", "favor"}


def _frase_pon(user_text: str) -> str:
    """'Ponme qué hace Escocia en YouTube' -> 'qué hace Escocia'; '' si no es una orden de poner algo."""
    m = re.match(r"^\s*(pon|ponme|reproduce|reproduceme|quiero (escuchar|ver))\s+(.+)$", user_text.strip(" .!¡"),
                 flags=re.I)
    if not m:
        return ""
    frase = re.sub(r"\s+en (youtube( music)?|spotify)\s*$", "", m.group(3), flags=re.I)
    # Recomendados: que decida el modelo. (Varias peticiones ya se excluyen al haber varias llamadas;
    # una " y " no basta: "qué hace que Escocia y…" también es un título.)
    if _RECOMENDADO.search(" ".join(_norm(frase))):
        return ""
    return frase.strip()


_CIFRA = re.compile(r"\d|\b(cero|diez|veinte|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa|cien|mitad"
                    r"|maximo|minimo|tope)\b")
_RECOMENDADO = re.compile(r"recomend|sugerid|derecha|(siguiente|proximo|otro) video|opcion")
_DESCARGA = re.compile(r"descarg|\bbaja(r|la|lo|me)?\b|\bb[áa]jame|guarda|\bmp3\b", re.I)


def prune(calls: list[dict], user_text: str = "") -> list[dict]:
    """Corrige llamadas que el modelo pequeño elige mal.

    - Si va a reproducir algo, abrir/buscar en la web solo abriría otra pestaña
      ("abre YouTube y pon Bad Bunny").
    - "Ponme la canción X" a veces acababa en descargar_video: sin un verbo de descarga,
      se reproduce en vez de descargar.
    """
    import json
    texto = " ".join(_norm(user_text))
    for c in calls:
        f = c["function"]
        args = f.get("arguments") or "{}"
        args = json.loads(args) if isinstance(args, str) else dict(args)
        if not user_text:
            continue
        if f["name"] == "descargar_video" and not _DESCARGA.search(user_text):
            consulta = re.sub(r"\s+en youtube\s*$", "", args.get("url_o_busqueda", ""), flags=re.I)
            f["name"], args = "reproducir", {"consulta": consulta}
        frase = _frase_pon(user_text) if len(calls) == 1 else ""
        # "Ponme qué hace Escocia" acababa en buscar_info: "pon…" es siempre reproducir.
        if frase and f["name"] in ("buscar_info", "buscar_web", "responder", "reproducir"):
            # …con la frase completa: el modelo recortaba "qué hace que Escocia parezca…".
            plataforma = args.get("plataforma") if f["name"] == "reproducir" else None
            f["name"], args = "reproducir", {"consulta": frase}
            if plataforma:
                args["plataforma"] = plataforma
        # "Abre la noticia de los drones" acababa en abrir_web con una URL inventada por el modelo.
        if f["name"] in ("abrir_web", "buscar_web") and re.search(r"\bnoticia", texto) and _News.items:
            consulta = re.sub(r"^.*?\bnoticias?\s+(de|del|sobre|que habla de|donde)?\s*", "", user_text.strip(" .?¿"),
                              flags=re.I)
            f["name"], args = "abrir_noticia", {"consulta": consulta}
        # Con YouTube abierto, "pon el video de X" acababa en youtube_recomendado.
        if f["name"] == "youtube_recomendado" and not _RECOMENDADO.search(texto):
            consulta = re.sub(r"^(pon|ponme|reproduce|abre)\s+", "", user_text.strip(" ."), flags=re.I)
            f["name"], args = "reproducir", {"consulta": consulta}
        # "El recomendado del yate": el modelo no pasaba el título.
        if f["name"] == "youtube_recomendado" and not args.get("titulo"):
            resto = [w for w in texto.split() if w not in _NO_TITULO and w not in _ORDINALES]
            if resto and not any(w in _ORDINALES for w in texto.split()):
                args["titulo"] = " ".join(resto)
        # "Sube el volumen/brillo" llegaba con nivel=50 o 100 inventado: sin una cifra dicha, no hay nivel.
        if f["name"] in ("controlar_medios", "brillo") and args.get("nivel") is not None and not _CIFRA.search(texto):
            args.pop("nivel")
            if args.get("accion") == "fijar":
                args["accion"] = "subir" if re.search(r"\b(sube|subele|aumenta|mas)\b", texto) else "bajar"
        # "Activa el wifi" llegaba sin estado y se alternaba (apagándolo si ya estaba encendido).
        if f["name"] == "ajuste_rapido" and args.get("estado") in (None, "", "alternar"):
            if re.search(r"\b(activa|activame|enciende|prende|pon|ponme|conecta|manten|mantener|deja)\b", texto):
                args["estado"] = "encender"
            elif re.search(r"\b(desactiva|apaga|quita|quitame|desconecta)\b", texto):
                args["estado"] = "apagar"
        # El modelo pasa plataforma="youtube" aunque no la digan, y así se ignoraba Spotify abierto.
        if f["name"] == "reproducir" and args.get("plataforma"):
            nombrada = {"youtube_music": "music", "spotify": "spotify"}.get(args["plataforma"], "youtube")
            if nombrada not in texto:
                args.pop("plataforma")
        f["arguments"] = json.dumps(args, ensure_ascii=False)
    names = {c["function"]["name"] for c in calls}
    if "youtube_recomendado" in names:
        calls = [c for c in calls if c["function"]["name"] not in ("reproducir", "controlar_medios")]
    if "reproducir" in names or "youtube_recomendado" in names:
        calls = [c for c in calls if c["function"]["name"] not in ("abrir_web", "buscar_web")]
    return calls


# Linux con Omarchy: herramientas para su escritorio (temas, escritorios, ajustes, capturas…).
EXTRA_RULES = ""
EXTRA_QUICK: list = []
EXTRA_OPEN: Callable[[str], str | None] | None = None
if LINUX and shutil.which("omarchy") and shutil.which("hyprctl"):
    from .linux import omarchy as _omarchy
    EXTRA_RULES, EXTRA_QUICK, EXTRA_OPEN = _omarchy.RULES, _omarchy.QUICK, _omarchy.abrir_app


def schemas() -> list[dict]:
    return [t["schema"] for t in TOOLS.values()]


def call(name: str, args: dict) -> str:
    entry = TOOLS.get(name)
    if entry is None:
        return f"Error: no existe la herramienta {name}"
    try:
        return str(entry["fn"](**(args or {})))
    except TypeError as e:
        return f"Error de argumentos en {name}: {e}"
    except Exception as e:
        log.exception("Herramienta %s falló", name)
        return f"Error al ejecutar {name}: {e}"
