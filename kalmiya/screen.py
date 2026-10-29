"""Leer lo que el usuario ve en pantalla con el OCR integrado de Windows.

Se usa para la columna de recomendados de YouTube: la lista que se puede descargar
sin sesión es distinta de la personalizada que ve el usuario, y Chrome no expone el
contenido de la página por accesibilidad salvo con un lector de pantalla activo.
"""
import asyncio
import ctypes
import ctypes.wintypes
import io
import logging
import re
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass

import win32gui

log = logging.getLogger(__name__)


@contextmanager
def _physical_pixels():
    """Coordenadas de ventana, captura y clic en píxeles reales (pantallas con escala 125 %, 150 %…)."""
    user32 = ctypes.windll.user32
    prev = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    try:
        yield
    finally:
        if prev:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(prev))


def click(x: int, y: int) -> None:
    """Clic izquierdo en (x, y) y devuelve el puntero adonde estaba."""
    user32 = ctypes.windll.user32
    with _physical_pixels():
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetCursorPos(x, y)
        time.sleep(0.03)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # botón abajo
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # botón arriba
        time.sleep(0.03)
        user32.SetCursorPos(pt.x, pt.y)


@dataclass
class Line:
    text: str
    x: int  # coordenadas de pantalla
    y: int
    h: int


def _ocr(png: bytes) -> list[tuple[str, float, float, float]]:
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    async def run():
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(png)
        await writer.store_async()
        writer.detach_stream()
        stream.seek(0)
        bmp = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()
        engine = OcrEngine.try_create_from_user_profile_languages()
        result = await engine.recognize_async(bmp)
        out = []
        for line in result.lines:
            r = [w.bounding_rect for w in line.words]
            out.append((line.text, min(b.x for b in r), min(b.y for b in r), max(b.height for b in r)))
        return out

    return asyncio.run(run())


def read_region(left: int, top: int, right: int, bottom: int, scale: float = 1.5) -> list[Line]:
    from PIL import Image, ImageGrab
    img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    if scale != 1:  # el OCR acierta más con texto un poco más grande
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return [Line(t, left + int(x / scale), top + int(y / scale), int(h / scale)) for t, x, y, h in _ocr(buf.getvalue())]


def window_lines(hwnd: int, x0: float = 0.0, x1: float = 1.0) -> list[Line]:
    """Texto de una franja vertical de la ventana (fracciones de su ancho)."""
    with _physical_pixels():
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        w = r - l
        return read_region(max(l, 0) + int(w * x0), max(t, 0), l + int(w * x1), b)


_HORA = re.compile(r"^\d{1,2}:\d{2}|^(ayer|hoy|lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo|\d+/\d+/\d+)",
                   re.I)


def whatsapp_chats(hwnd: int) -> tuple[list[Line], Line | None]:
    """Nombres de la lista de chats de WhatsApp (los que llevan hora/fecha a la derecha)
    y el cuadro de búsqueda."""
    lines = window_lines(hwnd, 0, 0.42)
    horas = [ln for ln in lines if _HORA.match(ln.text.strip())]
    nombres = [ln for ln in lines if not _HORA.match(ln.text.strip())
               and any(abs(h.y - ln.y) < 16 and h.x > ln.x + 200 for h in horas)]
    buscar = next((ln for ln in lines if ln.text.lower().startswith("buscar")), None)
    return nombres, buscar


@dataclass
class Item:
    title: str
    x: int
    y: int  # dónde hacer clic (primera línea del título)


_SKIP = re.compile(r"^(todos|nuevo|en vivo|mix|\d+:\d+)$", re.I)


def youtube_sidebar(hwnd: int) -> list[Item]:
    """Videos de la columna derecha de YouTube, de arriba abajo, tal como se ven."""
    with _physical_pixels():
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        width = r - l
        if width < 1000:  # ventana estrecha: YouTube pone los recomendados debajo del video
            return []
        lines = read_region(l + int(width * 0.66), t + 150, r - 20, b - 10)
    lines = [ln for ln in lines if not _SKIP.match(ln.text.strip())]
    if not lines:
        return []
    # El texto de cada video empieza en la misma columna (a la derecha de la miniatura);
    # lo que el OCR lee dentro de las miniaturas tiene otras x y se descarta.
    col = Counter(ln.x // 12 for ln in lines).most_common(1)[0][0] * 12
    lines = sorted((ln for ln in lines if abs(ln.x - col) <= 18), key=lambda ln: ln.y)
    items: list[list[Line]] = []
    for ln in lines:
        if items and ln.y - items[-1][-1].y < ln.h * 2.6:
            items[-1].append(ln)
        else:
            items.append([ln])
    out = []
    for group in items:
        # Título = líneas antes del canal/visitas ("13 M", "hace 4 d").
        title = []
        for ln in group:
            if re.search(r"\bhace \d|\d+(,\d+)? ?(M|K|mil) |visualizaciones", ln.text) and title:
                break
            title.append(ln.text)
        text = " ".join(title[:4])
        if len(text) > 6:
            out.append(Item(text, group[0].x + 40, group[0].y + group[0].h // 2))
    log.info("Recomendados en pantalla: %s", [i.title[:40] for i in out])
    return out
