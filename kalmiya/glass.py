"""Burbuja de texto estilo "liquid glass": vidrio esmerilado sobre lo que hay detrás,
con borde brillante, sombra suave y texto nítido.

El fondo se toma con una captura de la zona (que no incluye las ventanas por capas de
Kalmiya), se desenfoca y se tiñe; así no depende de efectos de Windows que Tk no soporta.
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageGrab

from .orb import LayeredWindow, hex_rgb

FONTS = r"C:\Windows\Fonts"


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for f in (name, "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(f"{FONTS}\\{f}", size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(text: str, font, max_w: int) -> list[str]:
    lines = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            test = f"{line} {word}".strip()
            if font.getlength(test) <= max_w or not line:
                line = test
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


class GlassBubble:
    MAX_W = 380  # ancho máximo del texto, en puntos

    def __init__(self, root, accent: str):
        self.win = LayeredWindow(root, 8)
        self.win.hide()
        self.s = LayeredWindow.screen_scale()
        self.accent = accent
        self.title_font = _font("seguisb.ttf", int(15 * self.s))
        self.body_font = _font("segoeui.ttf", int(13.5 * self.s))
        self.visible = False

    def render(self, title: str, body: str, right: int, center_y: int) -> None:
        """Dibuja la burbuja con su borde derecho en `right` y centrada en `center_y` (píxeles reales)."""
        s = self.s
        pad, radius, margin = int(18 * s), int(22 * s), int(24 * s)
        max_w = int(self.MAX_W * s)
        t_lines = _wrap(title, self.title_font, max_w) if title else []
        b_lines = _wrap(body, self.body_font, max_w) if body else []
        th = int(self.title_font.size * 1.35)
        bh = int(self.body_font.size * 1.45)
        text_w = max([self.title_font.getlength(l) for l in t_lines] + [self.body_font.getlength(l) for l in b_lines]
                     + [40 * s])
        w = int(text_w) + 2 * pad
        h = len(t_lines) * th + len(b_lines) * bh + 2 * pad - int(4 * s)
        W, H = w + 2 * margin, h + 2 * margin
        x0, y0 = right - W + margin, center_y - H // 2

        # 1) Fondo: lo que hay detrás, muy desenfocado y aclarado (vidrio esmerilado).
        try:
            bg = ImageGrab.grab(bbox=(x0, y0, x0 + W, y0 + H)).convert("RGB")
        except Exception:
            bg = Image.new("RGB", (W, H), (40, 40, 48))
        frost = bg.filter(ImageFilter.GaussianBlur(22 * s))
        f = np.asarray(frost, np.float32) / 255
        lum = f.mean(-1, keepdims=True)
        f = lum + (f - lum) * 1.35  # más saturado, como el vidrio de Apple
        # Se adapta a lo de atrás: vidrio claro con texto oscuro, o vidrio oscuro con texto claro.
        light_bg = float(lum.mean()) > 0.5
        if light_bg:
            f = f * 0.45 + np.float32(0.97) * 0.55
            title_fill, body_fill = (18, 19, 24), (60, 62, 72)
        else:
            f = f * 0.5 + np.array([0.06, 0.065, 0.08], np.float32) * 0.5
            title_fill, body_fill = (245, 246, 250), (210, 212, 222)
        f += hex_rgb(self.accent) * 0.035

        # 2) Brillo del vidrio: más claro arriba, y un borde fino iluminado.
        yy = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
        f += (1 - yy) * 0.06

        mask_big = Image.new("L", (W * 3, H * 3), 0)
        ImageDraw.Draw(mask_big).rounded_rectangle(
            (margin * 3, margin * 3, (margin + w) * 3, (margin + h) * 3), radius * 3, fill=255)
        mask = np.asarray(mask_big.resize((W, H), Image.LANCZOS), np.float32) / 255

        rim_big = Image.new("L", (W * 3, H * 3), 0)
        ImageDraw.Draw(rim_big).rounded_rectangle(
            (margin * 3, margin * 3, (margin + w) * 3, (margin + h) * 3), radius * 3, outline=255,
            width=max(3, int(3 * s)))
        rim = np.asarray(rim_big.resize((W, H), Image.LANCZOS), np.float32) / 255
        # El borde brilla más arriba a la izquierda (luz) y casi nada abajo.
        xx = np.linspace(0, 1, W, dtype=np.float32)[None, :]
        light = np.clip(1.15 - yy[..., 0] * 0.9 - xx * 0.35, 0.15, 1)
        f = f + (1 - f) * (rim * light * 0.55)[..., None]
        # Reflejo interior suave cerca del borde superior.
        inner = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(6 * s))
        edge = np.clip(mask - np.asarray(inner, np.float32) / 255, 0, 1)
        f = f + (1 - f) * (edge * (1 - yy[..., 0]) * 0.5)[..., None]

        # 3) Sombra suave debajo.
        shadow = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(12 * s))
        sh = np.asarray(shadow, np.float32) / 255
        sh = np.roll(sh, int(6 * s), axis=0) * 0.35

        img = Image.fromarray((np.clip(f, 0, 1) * 255).astype(np.uint8))
        # 4) Texto, alineado a la derecha (hacia el orbe).
        d = ImageDraw.Draw(img)
        y = margin + pad - int(2 * s)
        for line in t_lines:
            d.text((margin + w - pad - self.title_font.getlength(line), y), line, font=self.title_font,
                   fill=title_fill)
            y += th
        for line in b_lines:
            d.text((margin + w - pad - self.body_font.getlength(line), y), line, font=self.body_font,
                   fill=body_fill)
            y += bh

        rgb = np.asarray(img, np.float32) / 255
        alpha = np.clip(mask + sh * (1 - mask), 0, 1)
        col = rgb * mask[..., None]  # la sombra es negra
        out = np.empty((H, W, 4), np.uint8)
        pm = col * 255  # ya premultiplicado: color * máscara; la sombra aporta solo alfa
        out[..., 0], out[..., 1], out[..., 2] = pm[..., 2], pm[..., 1], pm[..., 0]
        out[..., 3] = alpha * 255
        self.win.show(out, x0, y0)
        self.visible = True

    def hide(self) -> None:
        self.win.hide()
        self.visible = False
