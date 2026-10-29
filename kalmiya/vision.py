"""Preguntar por una imagen de la pantalla: el usuario elige la imagen con un clic (o
arrastrando un recuadro) y LFM3-VL responde.

LFM3-VL corre en un segundo llama-server que se arranca la primera vez que se usa.
"""
import base64
import io
import logging
import subprocess
import time
import tkinter as tk
from typing import Callable

import httpx
from PIL import Image, ImageGrab

from .config import BIN_DIR, LOG_DIR, MODELS_DIR, Config

log = logging.getLogger(__name__)

SYSTEM = {
    "es": ("Eres Kalmiya, una asistente de voz. Te muestran una imagen de la pantalla del usuario. "
           "Responde en español, en una a tres frases cortas, sin markdown ni listas: se leerá en voz alta."),
    "en": ("You are Kalmiya, a voice assistant. You are shown an image from the user's screen. "
           "Answer in English, in one to three short sentences, no markdown or lists: it will be read aloud."),
}


class VisionServer:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.url = f"http://127.0.0.1:{cfg.vl_puerto}"

    @property
    def available(self) -> bool:
        return (MODELS_DIR / self.cfg.vl_gguf).exists() and (MODELS_DIR / self.cfg.vl_mmproj).exists()

    def start(self, timeout: float = 90) -> None:
        if self.proc and self.proc.poll() is None:
            return
        exe = next(BIN_DIR.rglob("llama-server.exe"), None)
        if exe is None or not self.available:
            raise RuntimeError("Falta el modelo de visión LFM3-VL. Ejecuta el instalador de nuevo.")
        cmd = [str(exe), "-m", str(MODELS_DIR / self.cfg.vl_gguf), "--mmproj", str(MODELS_DIR / self.cfg.vl_mmproj),
               "--port", str(self.cfg.vl_puerto), "--host", "127.0.0.1", "-c", "4096",
               "-ngl", str(self.cfg.llm_gpu_capas), "--jinja", "--temp", "0.1",
               "--chat-template-kwargs", '{"enable_thinking": false}']
        log.info("Iniciando visión: %s", " ".join(cmd))
        logf = open(LOG_DIR / "llama-vision.log", "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, cwd=str(exe.parent),
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("El modelo de visión se cerró; revisa logs/llama-vision.log")
            try:
                if httpx.get(f"{self.url}/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise RuntimeError("El modelo de visión no respondió a tiempo")

    def ask(self, image: Image.Image, question: str, history: list[dict] | None = None) -> str:
        self.start()
        img = image.convert("RGB")
        img.thumbnail((1024, 1024))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        lang = "en" if self.cfg.idioma.startswith("en") else "es"
        msgs = [{"role": "system", "content": SYSTEM[lang]},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                             {"type": "text", "text": question}]}]
        r = httpx.post(f"{self.url}/v1/chat/completions", timeout=120, json={
            "messages": msgs, "temperature": 0.1, "max_tokens": 300,
            "chat_template_kwargs": {"enable_thinking": False}})
        r.raise_for_status()
        return (r.json()["choices"][0]["message"].get("content") or "").strip()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def _element_box(x: int, y: int, screen: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """Recuadro de la imagen bajo el puntero según UI Automation, si es razonable."""
    try:
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            ctrl = auto.ControlFromPoint(x, y)
            for _ in range(3):  # la imagen o su contenedor inmediato
                if ctrl is None:
                    break
                r = ctrl.BoundingRectangle
                area = r.width() * r.height() / (screen[0] * screen[1])
                if ctrl.ControlTypeName == "ImageControl" and 0.01 < area < 0.8:
                    return r.left, r.top, r.right, r.bottom
                ctrl = ctrl.GetParentControl()
    except Exception as e:
        log.debug("UIA sin imagen bajo el puntero: %s", e)
    return None


class ImagePicker:
    """Oscurece la pantalla; un clic elige la zona alrededor del puntero, arrastrar elige un recuadro.
    Esc cancela. Llama a `done(imagen | None)` en el hilo de Tk."""

    CLICK_BOX = (640, 480)  # zona que se toma con un clic simple (a escala 100 %)

    def __init__(self, root: tk.Misc, accent: str, done: Callable[[Image.Image | None], None]):
        self.done = done
        self.accent = accent
        # La captura se hace antes de oscurecer, para que no salga el velo.
        self.shot = ImageGrab.grab()
        w = self.win = tk.Toplevel(root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.35)
        w.configure(bg="black")
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        self.scale = self.shot.width / sw  # Tk usa puntos lógicos; la captura, píxeles reales
        w.geometry(f"{sw}x{sh}+0+0")
        c = self.canvas = tk.Canvas(w, bg="black", highlightthickness=0, cursor="crosshair")
        c.pack(fill="both", expand=True)
        from .i18n import t
        c.create_text(sw // 2, 60, text=t("picker"),
                      fill="white", font=("Segoe UI Semibold", 18))
        self.start = None
        self.rect = None
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._drag)
        c.bind("<ButtonRelease-1>", self._release)
        w.bind("<Escape>", lambda e: self._finish(None))
        w.focus_force()

    def _press(self, e) -> None:
        self.start = (e.x, e.y)

    def _drag(self, e) -> None:
        if self.rect:
            self.canvas.delete(self.rect)
        x0, y0 = self.start
        self.rect = self.canvas.create_rectangle(x0, y0, e.x, e.y, outline=self.accent, width=3)

    def _release(self, e) -> None:
        x0, y0 = self.start
        x1, y1 = e.x, e.y
        if abs(x1 - x0) < 12 and abs(y1 - y0) < 12:  # clic simple
            self.win.withdraw()
            box = _element_box(int(e.x * self.scale), int(e.y * self.scale), self.shot.size)
            if box:  # la imagen como elemento de la interfaz (Fotos, WhatsApp, Explorador…)
                self._finish(self.shot.crop(box))
                return
            # Si no, la zona alrededor del puntero.
            from .dpi import px
            bw, bh = (px(v) / self.scale for v in self.CLICK_BOX)
            bw, bh = int(bw), int(bh)
            sw, sh = self.win.winfo_width(), self.win.winfo_height()
            x0 = min(max(0, e.x - bw // 2), max(0, sw - bw))
            y0 = min(max(0, e.y - bh // 2), max(0, sh - bh))
            x1, y1 = x0 + bw, y0 + bh
        box = [int(v * self.scale) for v in (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))]
        self._finish(self.shot.crop(box))

    def _finish(self, img) -> None:
        self.win.destroy()
        self.done(img)
