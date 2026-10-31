"""Qué tiene abierto el usuario en Hyprland: ventana activa (y su URL si es un navegador) y otras ventanas.

Misma interfaz que kalmiya/context.py (Windows). `hwnd` es aquí la dirección de la ventana en
Hyprland ("0x55d0…") y `proceso` su clase en minúsculas ("chromium", "spotify").

Wayland no deja leer la barra de direcciones por accesibilidad como UI Automation, así que la
URL se copia de la propia barra (Ctrl+L, Ctrl+C, Esc) y solo de la ventana que ya está al frente.
"""
import logging
import re
import time
from dataclasses import dataclass, field

from . import desktop

log = logging.getLogger(__name__)

# Clase de ventana -> nombre del navegador.
BROWSERS = {"chromium": "Chromium", "google-chrome": "Chrome", "brave-browser": "Brave", "firefox": "Firefox",
            "microsoft-edge": "Edge", "vivaldi-stable": "Vivaldi", "opera": "Opera", "zen": "Zen",
            "librewolf": "LibreWolf", "helium": "Helium"}
_IGNORE = {"kalmiya", "org.omarchy.screensaver", ""}
_KNOWN = {"spotify": "Spotify", "discord": "Discord", "telegramdesktop": "Telegram", "code": "Visual Studio Code",
          "org.telegram.desktop": "Telegram", "com.mitchellh.ghostty": "Terminal", "alacritty": "Terminal",
          "foot": "Terminal", "kitty": "Terminal", "obsidian": "Obsidian", "org.gnome.nautilus": "Archivos",
          "typora": "Typora", "signal": "Signal", "1password": "1Password"}


def _friendly_name(cls: str, title: str) -> str:
    if cls in BROWSERS:
        return BROWSERS[cls]
    if cls in _KNOWN:
        return _KNOWN[cls]
    m = re.match(r"^(chrome|brave|msedge)-(?P<host>[^_]+)__", cls)  # web apps de Omarchy (chromium --app)
    if m:
        host = m["host"].removeprefix("www.").removeprefix("web.").removeprefix("app.")
        return {"whatsapp.com": "WhatsApp", "youtube.com": "YouTube", "music.youtube.com": "YouTube Music",
                "chatgpt.com": "ChatGPT", "x.com": "X", "discord.com": "Discord"}.get(host, title or host)
    for app in desktop.desktop_apps().values():
        if app["wmclass"] == cls or app["id"].removesuffix(".desktop").lower() == cls:
            return app["name"]
    return cls.split(".")[-1].capitalize()


def clean_title(titulo: str, navegador: bool) -> str:
    titulo = re.sub(r"^\(\d+\)\s*", "", titulo)  # "(3500) " = notificaciones de YouTube
    if navegador:  # "Video - YouTube - Chromium" -> "Video - YouTube"
        titulo = re.sub(r"\s[-—]\s[^-—]*$", "", titulo)
    return titulo.strip()


@dataclass
class Window:
    hwnd: str  # dirección de Hyprland
    proceso: str  # clase de la ventana, en minúsculas
    titulo: str
    url: str = ""
    app: str = ""

    def __post_init__(self):
        self.titulo = clean_title(self.titulo, self.es_navegador)

    @property
    def es_navegador(self) -> bool:
        return self.proceso in BROWSERS

    @property
    def youtube_id(self) -> str:
        from urllib.parse import parse_qs, urlparse
        u = urlparse(self.url)
        if "youtube.com" in u.netloc and u.path == "/watch":
            return parse_qs(u.query).get("v", [""])[0]
        if u.netloc == "youtu.be":
            return u.path.strip("/")
        return ""


@dataclass
class Snapshot:
    activa: Window | None = None
    otras: list[Window] = field(default_factory=list)

    def describe(self) -> str:
        lines = []
        if self.activa:
            a = self.activa
            lines.append(f"Ventana activa: {a.app} — \"{a.titulo}\"" + (f" ({a.url})" if a.url else ""))
        else:
            lines.append("Ventana activa: el escritorio")
        apps = []
        for w in self.otras:
            if w.app not in apps:
                apps.append(w.app)
        if apps:
            lines.append("Otras apps abiertas: " + ", ".join(apps[:12]))
        return "\n".join(lines)

    def browser(self) -> Window | None:
        if self.activa and self.activa.es_navegador:
            return self.activa
        return next((w for w in self.otras if w.es_navegador), None)


def _window(c: dict) -> Window:
    cls = (c.get("class") or c.get("initialClass") or "").lower()
    return Window(c["address"], cls, c.get("title", ""), app=_friendly_name(cls, c.get("title", "")))


def capture(foreground: str | None = None, with_url: bool = True) -> Snapshot:
    """`foreground`: ventana activa tomada al pulsar el atajo."""
    t = time.time()
    snap = Snapshot()
    if foreground is None:
        a = desktop.active_window()
        foreground = a["address"] if a else ""
    for c in desktop.clients():
        cls = (c.get("class") or "").lower()
        if cls in _IGNORE or not c.get("title"):
            continue
        if cls in ("toplevel", "tk") and "kalmiya" in c["title"].lower():
            continue  # ventanas de la propia Kalmiya (presentación, configuración, calendario)
        w = _window(c)
        if c["address"] == foreground:
            snap.activa = w
        else:
            snap.otras.append(w)
    if with_url:
        read_url(snap)
    log.info("Contexto (%.2fs): %s", time.time() - t, snap.describe().replace("\n", " | "))
    return snap


def read_url(snap: Snapshot) -> None:
    """Rellena la URL del navegador si es la ventana activa (se lee de su barra de direcciones)."""
    a = snap.activa
    if a and a.es_navegador and not a.url:
        a.url = browser_url(a.hwnd)


def browser_url(hwnd: str) -> str:
    """URL de la pestaña visible: Ctrl+L, Ctrl+C y Esc sobre la barra, sin perder el portapapeles."""
    a = desktop.active_window()
    if not a or a["address"] != hwnd:
        return ""  # no se roba el foco solo para mirar
    previo = desktop.clipboard_get()
    marca = f"kalmiya-{time.time()}"
    desktop.clipboard_set(marca)
    desktop.send("ctrl+l")
    time.sleep(0.06)
    desktop.send("ctrl+c")
    url = ""
    for _ in range(10):
        time.sleep(0.03)
        url = desktop.clipboard_get() or ""
        if url and url != marca:
            break
    desktop.send("esc")
    # Esc deja el foco en la barra: devolverlo a la página (F6 iría a la barra lateral de pestañas).
    # Si no, la siguiente tecla ("f" de pantalla completa, espacio…) se escribía en la barra.
    desktop.send("shift+f6")
    if previo is not None:
        desktop.clipboard_set(previo)
    url = url.strip() if url != marca else ""
    if not re.match(r"^[a-z]+://|^[\w-]+(\.[\w-]+)+(/|$)", url):
        return ""
    if "://" not in url:
        url = "https://" + url
    return url


def browser_windows() -> list[str]:
    return [c["address"] for c in desktop.clients() if (c.get("class") or "").lower() in BROWSERS]


def find_tab(match_fn) -> tuple[str, str, float] | None:
    """Solo se ven las pestañas al frente de cada ventana (su título); las demás las busca `tab_search`."""
    best = None
    for c in desktop.clients():
        cls = (c.get("class") or "").lower()
        if cls not in BROWSERS:
            continue
        title = clean_title(c.get("title", ""), True)
        score = match_fn(title)
        if best is None or score > best[2]:
            best = (c["address"], title, score)
    return best


def tab_search(nombre: str, accion: str) -> str | None:
    """Cambia a (o cierra) la pestaña que mejor coincide con `nombre` usando el buscador de pestañas
    del navegador (Ctrl+Shift+A en Chromium; "% " en la barra de Firefox). Devuelve el navegador usado."""
    wins = [c for c in desktop.clients() if (c.get("class") or "").lower() in BROWSERS]
    if not wins:
        return None
    w = wins[0]
    cls = w["class"].lower()
    previa = clean_title(w.get("title", ""), True)
    desktop.focus(w["address"])
    _switch(cls, nombre)
    if accion == "cerrar":
        time.sleep(0.25)
        actual = clean_title((desktop.active_window() or {}).get("title", ""), True)
        if actual == previa and nombre.lower() not in actual.lower():
            return BROWSERS[cls]  # no la encontró: no cerrar la que estaba a la vista
        desktop.send("ctrl+w")
        if previa and nombre.lower() not in previa.lower():
            time.sleep(0.2)
            _switch(cls, previa)  # volver a la pestaña que estaba viendo
    return BROWSERS[cls]


def _switch(cls: str, texto: str) -> None:
    if cls in ("firefox", "librewolf", "zen"):
        desktop.send("ctrl+l")
        time.sleep(0.08)
        desktop.type_text(f"% {texto}")
        time.sleep(0.5)
        desktop.send("down")
        desktop.send("enter")
    else:
        desktop.send("ctrl+shift+a")
        time.sleep(0.4)
        desktop.type_text(texto)
        time.sleep(0.4)
        desktop.send("enter")


def tab_action(hwnd: str, name: str, action: str) -> bool:
    """Pestaña al frente de una ventana: cambiar a ella o cerrarla."""
    focus(hwnd)
    if action == "cerrar":
        desktop.send("ctrl+w")
    return True


def focus(hwnd: str) -> None:
    desktop.focus(hwnd)


def window_title(hwnd: str) -> str:
    c = desktop.client(hwnd)
    return clean_title(c.get("title", ""), True) if c else ""


def navigate(hwnd: str, url: str) -> None:
    """Abre `url` en la pestaña actual del navegador (no en una nueva). Se pega (no se teclea)
    para que el autocompletado de la barra no le añada nada."""
    focus(hwnd)
    previo = desktop.clipboard_get()
    desktop.clipboard_set(url)
    desktop.send("ctrl+l")
    time.sleep(0.08)
    desktop.send("ctrl+v")
    time.sleep(0.05)
    desktop.send("enter")
    if previo is not None:
        time.sleep(0.3)
        desktop.clipboard_set(previo)
