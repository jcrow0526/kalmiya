"""Atajos globales en Hyprland.

En Wayland una app no puede escuchar el teclado global (la librería `keyboard` de Windows),
así que los atajos son binds de Hyprland que avisan a Kalmiya por un socket local:

  ~/.config/hypr/kalmiya.lua  (lo genera Kalmiya; hyprland.lua lo carga con require)
      → kalmiya-ctl press agente / release agente / nueva / imagen / esc
      → 127.0.0.1:47653 (el mismo socket que impide abrir Kalmiya dos veces)

Para "mantener pulsado", Hyprland avisa al soltar la tecla principal aunque se suelten antes
Ctrl o Alt (bind de soltar con ignore_mods), y solo mientras Kalmiya está grabando.
"""
import logging
import os
import socket
import subprocess
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

PORT = 47653
CONF = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "hypr" / "kalmiya.lua"
CTL = Path.home() / ".local" / "bin" / "kalmiya-ctl"

_MODS = {"ctrl": "CTRL", "control": "CTRL", "alt": "ALT", "shift": "SHIFT", "super": "SUPER", "win": "SUPER"}
_KEYS = {"space": "SPACE", "esc": "Escape", "enter": "Return", "tab": "Tab"}


def to_hypr(combo: str) -> tuple[str, str] | None:
    """"ctrl+alt+space" -> ("CTRL + ALT + SPACE", "SPACE")."""
    parts = [p.strip().lower() for p in (combo or "").split("+") if p.strip()]
    if not parts:
        return None
    *mods, key = parts
    if any(m not in _MODS for m in mods):
        return None
    key = _KEYS.get(key, key.upper() if len(key) == 1 else key)
    return " + ".join([_MODS[m] for m in mods] + [key]), key


def render(cfg) -> str:
    lines = ["-- Atajos de Kalmiya. Lo genera Kalmiya al arrancar y al guardar su Configuración:",
             "-- cámbialos allí, no aquí.",
             f'local ctl = os.getenv("HOME") .. "/.local/bin/kalmiya-ctl"',
             "local talking = nil  -- atajo de hablar/dictar pulsado",
             "local function send(cmd) hl.exec_cmd(ctl .. \" \" .. cmd) end",
             "local function press(mode) return function() talking = mode; send(\"press \" .. mode) end end",
             "local function release() if talking then send(\"release \" .. talking); talking = nil end end",
             "kalmiya_busy = kalmiya_busy or false",
             ""]
    released = set()
    for mode, combo, desc in (("agente", cfg.hotkey_agente, "Kalmiya: hablar"),
                              ("dictado", cfg.hotkey_dictado, "Kalmiya: dictar")):
        hk = to_hypr(combo)
        if not hk:
            continue
        keys, key = hk
        lines.append(f'o.bind("{keys}", "{desc}", press("{mode}"))')
        if key not in released:  # soltar la tecla principal, con o sin Ctrl/Alt todavía pulsados
            lines.append(f'hl.bind("{key}", release, {{ release = true, ignore_mods = true, non_consuming = true }})')
            released.add(key)
    for cmd, combo, desc in (("nueva", cfg.hotkey_nueva, "Kalmiya: nueva conversación"),
                             ("imagen", cfg.hotkey_imagen, "Kalmiya: preguntar por una imagen")):
        hk = to_hypr(combo)
        if hk:
            lines.append(f'o.bind("{hk[0]}", "{desc}", function() send("{cmd}") end)')
    lines += ["-- Esc la calla solo mientras habla o piensa (Kalmiya cambia kalmiya_busy); si no, Esc sigue",
              "-- llegando a la ventana activa.",
              'hl.bind("Escape", function() if kalmiya_busy then send("esc") end end, { non_consuming = true })',
              ""]
    return "\n".join(lines)


def install(cfg) -> None:
    """Escribe kalmiya.lua si cambió (Hyprland lo recarga solo al guardarse)."""
    text = render(cfg)
    try:
        if CONF.exists() and CONF.read_text(encoding="utf-8") == text:
            return
        CONF.parent.mkdir(parents=True, exist_ok=True)
        CONF.write_text(text, encoding="utf-8")
        subprocess.run(["hyprctl", "reload", "config-only"], capture_output=True, timeout=5)
        errors = subprocess.run(["hyprctl", "configerrors"], capture_output=True, text=True, timeout=5).stdout
        if "kalmiya" in errors:
            log.warning("Hyprland no aceptó los atajos: %s", errors.strip())
    except Exception as e:
        log.warning("No pude registrar los atajos en Hyprland: %s", e)


def set_busy(busy: bool) -> None:
    """Esc solo se intercepta mientras Kalmiya habla o piensa."""
    try:
        subprocess.Popen(["hyprctl", "eval", f"kalmiya_busy = {'true' if busy else 'false'}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


class ControlServer:
    """Recibe órdenes de una línea ("press agente") de kalmiya-ctl."""

    def __init__(self, sock: socket.socket, handler: Callable[[str, list[str]], None]):
        self.sock = sock
        self.handler = handler
        sock.listen(8)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(1)
                try:
                    data = conn.recv(256).decode("utf-8", "replace").strip()
                except OSError:
                    continue
            if data:
                cmd, *args = data.split()
                try:
                    self.handler(cmd, args)
                except Exception:
                    log.exception("Orden %s falló", data)


def send(command: str) -> bool:
    """Manda una orden a la Kalmiya que ya está abierta (False si no hay ninguna)."""
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=1) as s:
            s.sendall(command.encode() + b"\n")
        return True
    except OSError:
        return False
