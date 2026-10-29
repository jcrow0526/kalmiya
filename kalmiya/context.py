"""Qué tiene abierto el usuario: ventana activa (y su URL si es un navegador) y otras ventanas.

Se captura al pulsar el atajo, antes de que Kalmiya muestre su indicador, para que
"pon el primer recomendado" o "descarga este video" actúen sobre lo que el usuario ve.
"""
import ctypes
import functools
import logging
import re
import time
from dataclasses import dataclass, field

import psutil
import win32api
import win32con
import win32gui
import win32process

log = logging.getLogger(__name__)

BROWSERS = {"brave.exe": "Brave", "chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox",
            "opera.exe": "Opera", "vivaldi.exe": "Vivaldi"}
# Ventanas del sistema que no interesan al modelo.
_IGNORE = {"explorer.exe", "textinputhost.exe", "applicationframehost.exe", "systemsettings.exe",
           "nvidia overlay.exe", "shellexperiencehost.exe", "searchhost.exe", "startmenuexperiencehost.exe",
           "pythonw.exe", "python.exe", "lockapp.exe"}


# Apps de la Tienda cuyo .exe no se puede leer (WindowsApps) o con nombre poco claro.
_KNOWN = {"whatsapp.root.exe": "WhatsApp", "whatsapp.exe": "WhatsApp", "spotify.exe": "Spotify",
          "telegram.exe": "Telegram", "discord.exe": "Discord", "code.exe": "Visual Studio Code"}


@functools.lru_cache(maxsize=128)
def _friendly_name(proceso: str, exe: str) -> str:
    """Nombre legible del programa ("Visual Studio Code" en vez de "code.exe")."""
    if proceso in BROWSERS:
        return BROWSERS[proceso]
    if proceso in _KNOWN:
        return _KNOWN[proceso]
    try:
        lang, cp = win32api.GetFileVersionInfo(exe, "\\VarFileInfo\\Translation")[0]
        desc = win32api.GetFileVersionInfo(exe, f"\\StringFileInfo\\{lang:04x}{cp:04x}\\FileDescription")
        if desc and len(desc) < 40:
            return desc.strip()
    except Exception:
        pass
    return proceso.removesuffix(".exe").split(".")[0].capitalize()


def clean_title(titulo: str, navegador: bool) -> str:
    titulo = re.sub(r"^\(\d+\)\s*", "", titulo)  # "(3500) " = notificaciones de YouTube
    if navegador:  # "Video - YouTube - Google Chrome" -> "Video - YouTube"
        titulo = re.sub(r"\s[-—]\s[^-—]*$", "", titulo)
    return titulo.strip()


@dataclass
class Window:
    hwnd: int
    proceso: str  # "brave.exe"
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
        """Texto para el prompt del modelo."""
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
        """La ventana de navegador más reciente (la activa si lo es)."""
        if self.activa and self.activa.es_navegador:
            return self.activa
        return next((w for w in self.otras if w.es_navegador), None)


def _process(hwnd: int) -> tuple[str, str]:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        p = psutil.Process(pid)
        name = p.name().lower()
        try:
            exe = p.exe()
        except Exception:
            exe = ""
        return name, _friendly_name(name, exe)
    except Exception:
        return "", ""


def _visible_windows() -> list[int]:
    """Ventanas principales visibles en orden Z (la de arriba primero)."""
    out = []
    hwnd = win32gui.GetTopWindow(None)
    while hwnd:
        if (win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd)
                and not win32gui.GetWindow(hwnd, win32con.GW_OWNER)
                and not win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW):
            out.append(hwnd)
        hwnd = win32gui.GetWindow(hwnd, win32con.GW_HWNDNEXT)
    return out


def browser_url(hwnd: int) -> str:
    """URL de la pestaña visible leyendo la barra de direcciones por UI Automation."""
    try:
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            win = auto.ControlFromHandle(hwnd)
            # Chromium: la barra es el primer Edit de la ventana; Firefox igual (nombre distinto).
            edit = win.EditControl(searchDepth=12)
            if not edit.Exists(0.5, 0.05):
                return ""
            value = edit.GetValuePattern().Value or ""
    except Exception as e:
        log.debug("No pude leer la URL: %s", e)
        return ""
    if value and "://" not in value and "." in value.split("/")[0]:
        value = "https://" + value  # Chromium oculta "https://"
    return value


def browser_windows() -> list[int]:
    """Ventanas de navegador abiertas, la de más arriba primero."""
    return [h for h in _visible_windows() if _process(h)[0] in BROWSERS]


def _tab_items(win):
    import uiautomation as auto
    return [c for c, _ in auto.WalkControl(win, maxDepth=14) if c.ControlTypeName == "TabItemControl"]


def find_tab(match_fn) -> tuple[int, str, float] | None:
    """Pestaña (de cualquier ventana de navegador) cuyo título mejor coincide: (hwnd, título, puntuación)."""
    import uiautomation as auto
    best = None
    with auto.UIAutomationInitializerInThread():
        for hwnd in browser_windows():
            for tab in _tab_items(auto.ControlFromHandle(hwnd)):
                score = match_fn(clean_title(tab.Name, False))
                if best is None or score > best[2]:
                    best = (hwnd, tab.Name, score)
    return best


def tab_action(hwnd: int, name: str, action: str) -> bool:
    """`action`: "cerrar" (con su botón ✕, sin cambiar de pestaña) o "cambiar" (la pone al frente)."""
    import keyboard
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        tab = next((t for t in _tab_items(auto.ControlFromHandle(hwnd)) if t.Name == name), None)
        if tab is None:
            return False
        if action == "cerrar":
            for c, _ in auto.WalkControl(tab, maxDepth=3):
                if c.ControlTypeName == "ButtonControl" and re.search(r"cerrar|close", c.Name or "", re.I):
                    c.GetInvokePattern().Invoke()
                    return True
        # Cambiar (o cerrar sin botón visible): seleccionarla y, si toca, Ctrl+W.
        try:
            tab.GetSelectionItemPattern().Select()
        except Exception:
            tab.Click(simulateMove=False)
    if action == "cerrar":
        focus(hwnd)
        keyboard.send("ctrl+w")
    else:
        focus(hwnd)
    return True


def capture(foreground: int | None = None, with_url: bool = True) -> Snapshot:
    """`foreground`: ventana activa tomada al pulsar el atajo (antes de que aparezca el indicador)."""
    t = time.time()
    snap = Snapshot()
    fg = foreground or win32gui.GetForegroundWindow()
    for hwnd in _visible_windows():
        proc, app = _process(hwnd)
        if not proc or proc in _IGNORE:
            continue
        w = Window(hwnd, proc, win32gui.GetWindowText(hwnd), app=app)
        if hwnd == fg:
            snap.activa = w
        else:
            snap.otras.append(w)
    b = snap.browser()
    if with_url and b:
        b.url = browser_url(b.hwnd)
    log.info("Contexto (%.2fs): %s", time.time() - t, snap.describe().replace("\n", " | "))
    return snap


def focus(hwnd: int) -> None:
    """Trae una ventana al frente (Windows lo bloquea si no "tocamos" el teclado antes)."""
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # Alt abajo/arriba: permite SetForegroundWindow
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.12)


def window_title(hwnd: int) -> str:
    try:
        return clean_title(win32gui.GetWindowText(hwnd), True)
    except Exception:
        return ""


def navigate(hwnd: int, url: str) -> None:
    """Abre `url` en la pestaña actual del navegador (no en una nueva)."""
    import keyboard
    import pyperclip
    focus(hwnd)
    try:
        previo = pyperclip.paste()
    except Exception:
        previo = None
    pyperclip.copy(url)
    keyboard.send("ctrl+l")
    time.sleep(0.08)
    keyboard.send("ctrl+v")
    time.sleep(0.05)
    keyboard.send("enter")
    if previo is not None:
        time.sleep(0.3)
        pyperclip.copy(previo)
