"""Leer lo que el usuario ve en pantalla con Tesseract (en Windows se usa su OCR integrado).

Misma interfaz que kalmiya/screen.py. Las coordenadas son las lógicas de Hyprland; la
captura de grim sale en píxeles reales y se convierte.
"""
import logging
import os
import re
import shutil
import subprocess
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass

from ..config import MODELS_DIR
from . import desktop

log = logging.getLogger(__name__)

TESSDATA = MODELS_DIR / "tessdata"


@contextmanager
def _physical_pixels():
    yield  # Hyprland ya da coordenadas coherentes para capturas, clics y ventanas


def click(x: int, y: int) -> None:
    desktop.click(x, y)


@dataclass
class Line:
    text: str
    x: int
    y: int
    h: int


def _langs() -> tuple[list[str], str | None]:
    """Idiomas de Tesseract disponibles: los de Kalmiya (spa+eng) o los del sistema."""
    if (TESSDATA / "spa.traineddata").exists():
        langs = [l for l in ("spa", "eng") if (TESSDATA / f"{l}.traineddata").exists()]
        return langs, str(TESSDATA)
    return ["eng"], None


def _ocr(img) -> list[tuple[str, float, float, float]]:
    """Líneas de texto: (texto, x, y, alto) en píxeles de la imagen."""
    if shutil.which("tesseract") is None:
        raise RuntimeError("Falta tesseract (sudo pacman -S tesseract tesseract-data-spa)")
    import io
    buf = io.BytesIO()
    img.save(buf, "PNG")
    langs, tessdir = _langs()
    # TSV con -c y no con la config "tsv": esa vive en el tessdata del sistema, no en el de Kalmiya.
    cmd = ["tesseract", "stdin", "stdout", "-l", "+".join(langs), "--psm", "11", "-c", "tessedit_create_tsv=1"]
    if tessdir:
        cmd[1:1] = ["--tessdata-dir", tessdir]
    env = dict(os.environ, OMP_THREAD_LIMIT="2")
    r = subprocess.run(cmd, input=buf.getvalue(), capture_output=True, timeout=20, env=env)
    lines: dict[tuple, list] = {}
    for row in r.stdout.decode("utf-8", "replace").splitlines()[1:]:
        p = row.split("\t")
        if len(p) < 12 or not p[11].strip() or float(p[10]) < 30:
            continue
        key = (p[2], p[3], p[4])  # bloque, párrafo, línea
        lines.setdefault(key, []).append((p[11], int(p[6]), int(p[7]), int(p[8]), int(p[9])))
    out = []
    for words in lines.values():
        words.sort(key=lambda w: w[1])
        out.append((" ".join(w[0] for w in words), min(w[1] for w in words), min(w[2] for w in words),
                    max(w[4] for w in words)))
    return sorted(out, key=lambda l: (l[2], l[1]))


def read_region(left: int, top: int, right: int, bottom: int, scale: float = 1.5) -> list[Line]:
    from PIL import Image
    img = desktop.grab((left, top, right, bottom))
    k = img.width / max(1, right - left)  # píxeles reales por punto lógico
    if scale != 1:  # el OCR acierta más con texto un poco más grande
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    f = k * scale
    return [Line(t, left + int(x / f), top + int(y / f), int(h / f)) for t, x, y, h in _ocr(img)]


def _rect(hwnd: str) -> tuple[int, int, int, int]:
    c = desktop.client(hwnd)
    if not c:
        raise RuntimeError("La ventana ya no existe")
    (x, y), (w, h) = c["at"], c["size"]
    return x, y, x + w, y + h


def window_lines(hwnd: str, x0: float = 0.0, x1: float = 1.0) -> list[Line]:
    l, t, r, b = _rect(hwnd)
    w = r - l
    return read_region(l + int(w * x0), t, l + int(w * x1), b)


_HORA = re.compile(r"^\d{1,2}:\d{2}|^(ayer|hoy|lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo|\d+/\d+/\d+)",
                   re.I)


def whatsapp_chats(hwnd: str) -> tuple[list[Line], Line | None]:
    """Nombres de la lista de chats de WhatsApp Web (los que llevan hora/fecha a la derecha)
    y el cuadro de búsqueda."""
    lines = window_lines(hwnd, 0, 0.42)
    horas = [ln for ln in lines if _HORA.match(ln.text.strip())]
    nombres = [ln for ln in lines if not _HORA.match(ln.text.strip())
               and any(abs(h.y - ln.y) < 16 and h.x > ln.x + 150 for h in horas)]
    buscar = next((ln for ln in lines if ln.text.lower().startswith(("buscar", "search"))), None)
    return nombres, buscar


@dataclass
class Item:
    title: str
    x: int
    y: int


_SKIP = re.compile(r"^(todos|nuevo|en vivo|mix|\d+:\d+)$", re.I)


def youtube_sidebar(hwnd: str) -> list[Item]:
    """Videos de la columna derecha de YouTube, de arriba abajo, tal como se ven."""
    l, t, r, b = _rect(hwnd)
    width = r - l
    if width < 1000:  # ventana estrecha: YouTube pone los recomendados debajo del video
        return []
    lines = read_region(l + int(width * 0.66), t + 150, r - 20, b - 10)
    lines = [ln for ln in lines if not _SKIP.match(ln.text.strip())]
    if not lines:
        return []
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
        title = []
        for ln in group:
            if re.search(r"\bhace \d|\d+(,\d+)? ?(M|K|mil) |visualizaciones|views|ago\b", ln.text) and title:
                break
            title.append(ln.text)
        text = " ".join(title[:4])
        if len(text) > 6:
            out.append(Item(text, group[0].x + 40, group[0].y + group[0].h // 2))
    log.info("Recomendados en pantalla: %s", [i.title[:40] for i in out])
    return out
