"""Lado de la app del orbe en Wayland: arranca kalmiya.linux.overlay y le manda órdenes."""
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..config import LOG_DIR

log = logging.getLogger(__name__)

LAYER_SHELL = next((p for p in ("/usr/lib/libgtk4-layer-shell.so", "/usr/lib64/libgtk4-layer-shell.so",
                                "/usr/lib/x86_64-linux-gnu/libgtk4-layer-shell.so") if Path(p).exists()), "")


class Overlay:
    def __init__(self, color: str, level_fn):
        self.color = color
        self.level_fn = level_fn
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.mode = "idle"
        self.visible = False
        threading.Thread(target=self._levels, daemon=True).start()

    def _ensure(self) -> bool:
        if self.proc and self.proc.poll() is None:
            return True
        if not LAYER_SHELL:
            log.warning("Falta gtk4-layer-shell: no se puede mostrar el orbe")
            return False
        env = dict(os.environ, LD_PRELOAD=LAYER_SHELL, GDK_BACKEND="wayland")
        root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        logf = open(LOG_DIR / "overlay.log", "a", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen([sys.executable, "-m", "kalmiya.linux.overlay", self.color],
                                     stdin=subprocess.PIPE, stdout=logf, stderr=subprocess.STDOUT, env=env,
                                     text=True, bufsize=1)
        return True

    def _send(self, **msg) -> None:
        with self._lock:
            if not self._ensure():
                return
            try:
                self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                self.proc = None  # se reinicia en la siguiente orden

    def start(self) -> None:
        with self._lock:
            self._ensure()

    def status(self, title: str, body: str, mode: str) -> None:
        self.mode, self.visible = mode, True
        self._send(op="status", title=title, body=body, mode=mode)

    def hide(self) -> None:
        self.mode, self.visible = "idle", False
        self._send(op="hide")

    def set_color(self, color: str) -> None:
        self.color = color
        self._send(op="color", color=color)

    def _levels(self) -> None:
        """Nivel del micrófono para que el orbe se mueva con la voz."""
        while True:
            time.sleep(0.04)
            if self.mode == "listening" and self.proc:
                self._send(op="level", value=round(float(self.level_fn()), 4))

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self._send(op="quit")
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
