"""Interfaz: indicador flotante, icono de bandeja y ventana de configuración."""
import os
import queue
import tkinter as tk
from tkinter import ttk
from typing import Callable

from PIL import Image, ImageDraw

from .config import APP_NAME, DOWNLOADS_DIR, LOG_DIR, Config
from .glass import GlassBubble
from .i18n import VOICES, t
from .orb import DEFAULT_COLOR, Orb, OrbRenderer

BG, FG, ACCENT, MUTED = "#16161d", "#f2f2f5", "#7c6cff", "#9a9aab"


def make_icon(color: str = DEFAULT_COLOR, size: int = 64) -> Image.Image:
    """Icono = un fotograma del orbe."""
    import numpy as np
    f = OrbRenderer(size * 2, color).frame(1.2, 0.0)  # BGRA premultiplicado
    a = f[..., 3:4].astype(np.float32)
    rgb = np.where(a > 0, f[..., [2, 1, 0]].astype(np.float32) * 255 / np.maximum(a, 1), 0)
    img = Image.fromarray(np.concatenate([np.clip(rgb, 0, 255), a], -1).astype(np.uint8), "RGBA")
    # El orbe ocupa el 60 % del lienzo (el resto es brillo): recortar para que se vea grande.
    m = int(size * 2 * 0.17)
    return img.crop((m, m, size * 2 - m, size * 2 - m)).resize((size, size), Image.LANCZOS)


class UI:
    """Todo lo de Tk corre en el hilo principal; otros hilos usan `post()`."""

    def __init__(self, cfg: Config, level_fn: Callable[[], float]):
        self.cfg = cfg
        self.level_fn = level_fn
        self.q: queue.Queue = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self._build_overlay()
        self._hide_job = None
        self._listening = False
        self.on_settings_saved: Callable[[], None] = lambda: None
        self.on_quit: Callable[[], None] = lambda: None
        self.on_reset: Callable[[], None] = lambda: None
        self.on_calendar: Callable[[], None] = lambda: None
        self.on_reminders: Callable[[], None] = lambda: None
        self.on_settings: Callable[[], None] = lambda: None
        self.on_benchmark: Callable[[], None] = lambda: None
        self.on_welcome: Callable[[], None] = lambda: None
        self.root.after(30, self._pump)

    # ------------------------------------------------------------ overlay
    def _build_overlay(self) -> None:
        self.orb = Orb(self.root, self.cfg.color_orbe or DEFAULT_COLOR, self.level_fn)
        # Burbuja de vidrio a la izquierda del orbe.
        self.bubble = GlassBubble(self.root, self.cfg.color_orbe or DEFAULT_COLOR)
        self._bubble_text: tuple[str, str] | None = None

    def _place_bubble(self, title: str, body: str) -> None:
        if self._bubble_text == (title, body) and self.bubble.visible:
            return
        self._bubble_text = (title, body)
        ox, oy = self.orb.position
        self.bubble.render(title, body, right=ox + int(self.orb.px * 0.12), center_y=oy + self.orb.px // 2)

    def _show(self, title: str, body: str = "", listening: bool = False, hide_after: int | None = None,
              mode: str | None = None) -> None:
        if self._hide_job:
            self.root.after_cancel(self._hide_job)
            self._hide_job = None
        self._listening = listening
        if mode is None:
            mode = "listening" if listening else ("speaking" if hide_after else "thinking")
        self.orb.show(mode)
        if title or body:
            self._place_bubble(title, body)
        else:
            self.bubble.hide()
        if hide_after:
            self._hide_job = self.root.after(hide_after, self._hide)

    def _hide(self) -> None:
        self._listening = False
        self.bubble.hide()
        self.orb.hide()

    def set_color(self, color: str) -> None:
        def apply():
            self.orb.set_color(color)
            self.bubble.accent = color
        self.post(apply)

    # ------------------------------------------------------------ API para hilos
    def post(self, fn: Callable, *args, **kw) -> None:
        self.q.put((fn, args, kw))

    def status(self, title: str, body: str = "", listening: bool = False, hide_after: int | None = None,
               mode: str | None = None):
        self.post(self._show, title, body, listening, hide_after, mode)

    def hide(self):
        self.post(self._hide)

    def _pump(self) -> None:
        try:
            while True:
                fn, args, kw = self.q.get_nowait()
                fn(*args, **kw)
        except queue.Empty:
            pass
        self.root.after(30, self._pump)

    # ------------------------------------------------------------ bandeja
    def start_tray(self) -> None:
        import pystray
        item = pystray.MenuItem
        menu = pystray.Menu(
            item(f"{APP_NAME} — {t('tray_talk', k=self.cfg.hotkey_agente)}", None, enabled=False),
            item(t("tray_calendar"), lambda: self.post(self.on_calendar)),
            item(t("tray_reminders"), lambda: self.post(self.on_reminders)),
            item(t("tray_settings"), lambda: self.post(self.on_settings)),
            item(t("tray_new"), lambda: self.on_reset()),
            item(t("tray_welcome"), lambda: self.post(self.on_welcome)),
            item(t("tray_downloads"), lambda: os.startfile(str(DOWNLOADS_DIR)) if DOWNLOADS_DIR.exists() else None),
            item(t("tray_logs"), lambda: os.startfile(str(LOG_DIR))),
            item(t("tray_bench"), lambda: self.on_benchmark()),
            pystray.Menu.SEPARATOR,
            item(t("tray_quit"), lambda: self.post(self._quit)),
        )
        self.tray = pystray.Icon(APP_NAME, make_icon(self.cfg.color_orbe), APP_NAME, menu)
        self.tray.run_detached()

    def set_tray_color(self, color: str) -> None:
        try:
            self.tray.icon = make_icon(color)
        except Exception:
            pass

    def notify(self, msg: str) -> None:
        try:
            self.tray.notify(msg, APP_NAME)
        except Exception:
            pass

    def _quit(self) -> None:
        try:
            self.tray.stop()
        except Exception:
            pass
        self.on_quit()
        self.root.quit()

