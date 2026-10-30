"""Escritorio Linux (Wayland + Hyprland): ventanas, teclas, portapapeles, capturas y apps.

Equivale a lo que en Windows hacen win32gui, `keyboard`, pyperclip y os.startfile:
- ventanas: `hyprctl clients/activewindow -j` y dispatchers de Hyprland (config en Lua);
- teclas y texto: `wtype` (teclado virtual de Wayland);
- clics: el puntero se lleva con Hyprland y el botón se pulsa con un ratón virtual (uinput);
- portapapeles: wl-clipboard; capturas: grim.
"""
import configparser
import functools
import io
import json
import logging
import os
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)


def _run(cmd: list[str], timeout: float = 5, input: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, input=input)


# ---------------------------------------------------------------- Hyprland

def hypr_json(what: str):
    try:
        return json.loads(_run(["hyprctl", what, "-j"]).stdout or b"null")
    except Exception as e:
        log.debug("hyprctl %s: %s", what, e)
        return None


def dispatch(lua: str, legacy: list[str] | None = None) -> bool:
    """Dispatcher de Hyprland en Lua; si falla (config clásica), su forma antigua."""
    r = _run(["hyprctl", "dispatch", lua])
    if r.returncode == 0 and b"error" not in r.stdout.lower():
        return True
    if legacy:
        r = _run(["hyprctl", "dispatch", *legacy])
        return r.returncode == 0 and b"error" not in r.stdout.lower()
    return False


def hypr_eval(lua: str) -> bool:
    r = _run(["hyprctl", "eval", lua])
    return r.returncode == 0 and b"error" not in r.stdout.lower()


def _lua_str(s: str) -> str:
    return json.dumps(s)  # una cadena JSON también es una cadena válida de Lua


def clients() -> list[dict]:
    """Ventanas mapeadas, la usada más recientemente primero."""
    cl = [c for c in hypr_json("clients") or [] if c.get("mapped") and not c.get("hidden")]
    return sorted(cl, key=lambda c: c.get("focusHistoryID", 99))


def active_window() -> dict | None:
    w = hypr_json("activewindow")
    return w if w and w.get("address") else None


def client(address: str) -> dict | None:
    return next((c for c in clients() if c["address"] == address), None)


def focus(address: str) -> None:
    dispatch(f'hl.dsp.focus({{ window = "address:{address}" }})', ["focuswindow", f"address:{address}"])
    time.sleep(0.12)


def close_window(address: str) -> None:
    dispatch(f'hl.dsp.window.close({{ window = "address:{address}" }})', ["closewindow", f"address:{address}"])


def fullscreen(address: str, mode: str = "maximized") -> None:
    focus(address)
    dispatch(f'hl.dsp.window.fullscreen({{ mode = "{mode}" }})', ["fullscreen", "1" if mode == "maximized" else "0"])


def minimize(address: str) -> None:
    """Hyprland no minimiza: la ventana se manda al espacio especial "minimized"."""
    dispatch(f'hl.dsp.window.move({{ window = "address:{address}", workspace = "special:minimized", follow = false }})',
             ["movetoworkspacesilent", f"special:minimized,address:{address}"])


def focused_monitor() -> dict:
    mons = hypr_json("monitors") or []
    return next((m for m in mons if m.get("focused")), mons[0] if mons else
                {"name": "", "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.0})


def cursor_pos() -> tuple[int, int]:
    p = hypr_json("cursorpos") or {}
    return int(p.get("x", 0)), int(p.get("y", 0))


def move_cursor(x: int, y: int) -> None:
    dispatch(f"hl.dsp.cursor.move({{ x = {int(x)}, y = {int(y)} }})", ["movecursor", str(int(x)), str(int(y))])


# ---------------------------------------------------------------- teclado y ratón

_KEYS = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift", "super": "logo", "win": "logo"}
_NAMES = {"enter": "Return", "esc": "Escape", "escape": "Escape", "tab": "Tab", "space": "space", "end": "End",
          "home": "Home", "delete": "Delete", "backspace": "BackSpace", "left": "Left", "right": "Right",
          "up": "Up", "down": "Down", "f5": "F5", "f11": "F11", "/": "slash",
          "play/pause media": "XF86AudioPlay", "next track": "XF86AudioNext", "previous track": "XF86AudioPrev",
          "volume up": "XF86AudioRaiseVolume", "volume down": "XF86AudioLowerVolume", "volume mute": "XF86AudioMute"}


def send(combo: str) -> None:
    """Pulsa una combinación con la sintaxis de `keyboard` ("ctrl+l", "shift+n", "enter")."""
    if combo in _NAMES:
        parts = [combo]
    else:
        parts = combo.lower().split("+")
    mods, key = parts[:-1], parts[-1]
    cmd = ["wtype"]
    for m in mods:
        cmd += ["-M", _KEYS.get(m, m)]
    cmd += ["-k", _NAMES.get(key, key)]
    for m in reversed(mods):
        cmd += ["-m", _KEYS.get(m, m)]
    _run(cmd)


def type_text(text: str) -> None:
    """Escribe texto (con acentos y emojis) en la ventana con el foco."""
    if text:
        _run(["wtype", "-d", "2", "--", text], timeout=30)


_mouse = None
_mouse_lock = threading.Lock()


def _virtual_mouse():
    global _mouse
    with _mouse_lock:
        if _mouse is None:
            from evdev import UInput
            from evdev import ecodes as e
            _mouse = UInput({e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT], e.EV_REL: [e.REL_X, e.REL_Y]},
                            name="kalmiya-virtual-mouse")
            time.sleep(0.3)  # Hyprland tarda un poco en dar de alta el dispositivo nuevo
        return _mouse


def click(x: int, y: int) -> None:
    """Clic izquierdo en (x, y) (coordenadas lógicas de Hyprland) y el puntero vuelve adonde estaba."""
    from evdev import ecodes as e
    px, py = cursor_pos()
    move_cursor(x, y)
    time.sleep(0.05)
    try:
        m = _virtual_mouse()
        m.write(e.EV_KEY, e.BTN_LEFT, 1)
        m.syn()
        time.sleep(0.02)
        m.write(e.EV_KEY, e.BTN_LEFT, 0)
        m.syn()
    except Exception as ex:
        log.warning("No pude hacer clic (¿permiso de /dev/uinput?): %s", ex)
    time.sleep(0.05)
    move_cursor(px, py)


# ---------------------------------------------------------------- portapapeles

def clipboard_get() -> str | None:
    try:
        r = _run(["wl-paste", "--no-newline", "--type", "text"], timeout=2)
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None
    except Exception:
        return None


def clipboard_set(text: str) -> None:
    # wl-copy se queda en segundo plano sirviendo el contenido; no se espera a que termine.
    subprocess.Popen(["wl-copy", "--", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.05)


# ---------------------------------------------------------------- capturas

def grab(bbox: tuple[int, int, int, int] | None = None, output: str | None = None, timeout: float = 4):
    """Captura de pantalla como imagen PIL. `bbox` = (izq, arriba, der, abajo) en coordenadas lógicas;
    la imagen sale en píxeles reales (×escala del monitor)."""
    from PIL import Image
    cmd = ["grim"]
    if bbox:
        l, t, r, b = (int(v) for v in bbox)
        cmd += ["-g", f"{l},{t} {max(1, r - l)}x{max(1, b - t)}"]
    elif output:
        cmd += ["-o", output]
    cmd += ["-t", "ppm", "-"]
    r = _run(cmd, timeout=timeout)  # con el monitor apagado (DPMS) grim no termina nunca
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace").strip() or "grim falló")
    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


# ---------------------------------------------------------------- abrir cosas

def open_uri(target: str) -> bool:
    """Como os.startfile: abre un archivo, carpeta o URL con su app predeterminada."""
    if shutil.which("xdg-open") is None:
        return False
    subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return True


def handler_for(scheme: str) -> str:
    """App registrada para un esquema de URL ("" si no hay)."""
    try:
        r = _run(["xdg-mime", "query", "default", f"x-scheme-handler/{scheme}"], timeout=3)
        return r.stdout.decode().strip()
    except Exception:
        return ""


def _desktop_dirs() -> list[Path]:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    dirs = [data_home] + [Path(d) for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")]
    return [d / "applications" for d in dirs if (d / "applications").is_dir()]


@functools.lru_cache(maxsize=1)
def _desktop_cache(stamp: int) -> dict[str, dict]:
    apps: dict[str, dict] = {}
    for d in _desktop_dirs():
        for p in d.rglob("*.desktop"):
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read(p, encoding="utf-8")
                e = cp["Desktop Entry"]
            except Exception:
                continue
            if e.get("NoDisplay", "false").lower() == "true" or e.get("Hidden", "false").lower() == "true":
                continue
            if e.get("Type", "Application") != "Application" or not e.get("Exec"):
                continue
            info = {"name": e.get("Name", p.stem), "path": p, "id": p.name,
                    "wmclass": (e.get("StartupWMClass") or "").lower(), "exec": e.get("Exec", "")}
            names = {info["name"].lower(), p.stem.lower(), e.get("GenericName", "").lower()}
            for key in [k for k in e if k.startswith("name[")]:
                names.add(e[key].lower())
            for n in names:
                if n:
                    apps.setdefault(n, info)
    return apps


def desktop_apps() -> dict[str, dict]:
    """Apps instaladas por nombre en minúsculas (también el nombre traducido y el genérico)."""
    return _desktop_cache(int(time.time() // 60))  # se relee como mucho una vez por minuto


def launch(app: dict) -> None:
    """Lanza una app .desktop como lo hace Omarchy (uwsm-app), o con gtk-launch."""
    if shutil.which("uwsm-app"):
        cmd = ["uwsm-app", "--", app["id"]]
    elif shutil.which("gtk-launch"):
        cmd = ["gtk-launch", app["id"].removesuffix(".desktop")]
    else:
        exec_ = [a for a in shlex.split(app["exec"]) if not a.startswith("%")]
        cmd = exec_
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def launch_cmd(cmd: str) -> None:
    full = ["uwsm-app", "--", *shlex.split(cmd)] if shutil.which("uwsm-app") else shlex.split(cmd)
    subprocess.Popen(full, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


# ---------------------------------------------------------------- sonido y medios

def set_volume(percent: int) -> None:
    _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"])
    _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{max(0, min(100, percent)) / 100:.2f}"])


def change_volume(delta_percent: int) -> None:
    sign = "+" if delta_percent > 0 else "-"
    _run(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{abs(delta_percent)}%{sign}"])


def toggle_mute() -> None:
    _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"])


def _mpris_players() -> list[str]:
    r = _run(["busctl", "--user", "--json=short", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
              "org.freedesktop.DBus", "ListNames"])
    try:
        names = json.loads(r.stdout)["data"][0]
    except Exception:
        return []
    return [n for n in names if n.startswith("org.mpris.MediaPlayer2.")]


def _mpris_status(player: str) -> str:
    r = _run(["busctl", "--user", "--json=short", "get-property", player, "/org/mpris/MediaPlayer2",
              "org.mpris.MediaPlayer2.Player", "PlaybackStatus"])
    try:
        return json.loads(r.stdout)["data"]
    except Exception:
        return ""


def media(action: str) -> bool:
    """`action`: PlayPause | Next | Previous. Actúa sobre el reproductor que suena (MPRIS)."""
    if shutil.which("playerctl"):
        cmd = {"PlayPause": "play-pause", "Next": "next", "Previous": "previous"}[action]
        return _run(["playerctl", cmd]).returncode == 0
    players = _mpris_players()
    if not players:
        return False
    playing = [p for p in players if _mpris_status(p) == "Playing"]
    paused = [p for p in players if _mpris_status(p) == "Paused"]
    target = (playing or paused or players)[0]
    r = _run(["busctl", "--user", "call", target, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", action])
    return r.returncode == 0


def notify(title: str, body: str, icon: str = "") -> None:
    if shutil.which("notify-send"):
        cmd = ["notify-send", "-a", title] + (["-i", icon] if icon else []) + [title, body]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def lock_screen() -> None:
    for cmd in (["omarchy", "system", "lock"], ["loginctl", "lock-session"], ["hyprlock"]):
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return
