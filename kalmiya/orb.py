"""Orbe animado: se dibuja con numpy y se muestra en una ventana con transparencia por píxel.

El brillo exterior necesita alfa real (UpdateLayeredWindow); con el "color transparente"
de Tk quedaría un borde negro alrededor del resplandor.
"""
import ctypes
import ctypes.wintypes as wt
import math
import time
import tkinter as tk

import numpy as np

DEFAULT_COLOR = "#7CFF2B"  # verde lima


def hex_rgb(color: str) -> np.ndarray:
    c = color.lstrip("#")
    return np.array([int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)], dtype=np.float32)


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


class OrbRenderer:
    def __init__(self, size: int = 240, color: str = DEFAULT_COLOR):
        self.size = size
        self.set_color(color)
        y, x = np.mgrid[0:size, 0:size].astype(np.float32)
        self.x = (x - size / 2) / (size / 2)
        self.y = (y - size / 2) / (size / 2)
        self.r = np.hypot(self.x, self.y)
        self.th = np.arctan2(self.y, self.x)

    def set_color(self, color: str) -> None:
        self.base = hex_rgb(color)
        # Las zonas más brillantes van hacia blanco cálido, como el núcleo de la imagen de referencia.
        self.hot = np.clip(self.base * 0.4 + np.array([0.75, 0.8, 0.45], np.float32), 0, 1)

    @staticmethod
    def _filaments(px, py, pz, ts):
        """Remolinos de energía en 3D: brillan donde los senos deformados se anulan."""
        w1 = np.sin(2.4 * px + 1.8 * np.sin(2.0 * py + 0.8 * ts) + 1.2 * pz + 0.6 * ts)
        w2 = np.sin(2.7 * py - 1.6 * np.sin(2.2 * pz - 0.7 * ts) - 1.1 * px - 0.4 * ts)
        s = w1 + 0.8 * w2
        return np.exp(-s * s * 10.0) + 0.3 * np.exp(-s * s * 1.8)

    def frame(self, t: float, level: float = 0.0, energy: float = 1.0) -> np.ndarray:
        """Imagen BGRA premultiplicada de una esfera de vidrio con energía dentro.
        `level` 0-1 (voz) la hace latir; `energy` acelera el remolino (pensando)."""
        x, y, th = self.x, self.y, self.th
        R = 0.56 * (1 + 0.06 * level)
        # Casi esférica; solo con la voz se ondula un poco el borde.
        edge = R * (1 + 0.018 * level * np.sin(6 * th + 5 * t) + 0.005 * np.sin(3 * th + t))
        r = self.r / edge  # radio normalizado: < 1 dentro
        inside = _smoothstep(1.015, 0.985, r)
        rr = np.clip(r, 0, 1)
        z = np.sqrt(1 - rr * rr)  # profundidad de la superficie de la esfera
        nx, ny = x / edge, y / edge  # normal (nx, ny, z)

        # Puntos interiores a dos profundidades (cara frontal y trasera), girando en 3D:
        # al rotar se cruzan y dan sensación de volumen.
        ts = t * energy
        a, b = 0.35 * ts, 0.6 + 0.15 * math.sin(0.3 * ts)
        ca, sa, cb, sb = math.cos(a), math.sin(a), math.cos(b), math.sin(b)

        def rot(px, py, pz):
            px, pz = px * ca + pz * sa, -px * sa + pz * ca  # eje Y
            py, pz = py * cb - pz * sb, py * sb + pz * cb  # eje X
            return px, py, pz

        front = self._filaments(*rot(nx * 0.8, ny * 0.8, z * 0.55), ts)
        back = self._filaments(*rot(nx * 0.8, ny * 0.8, -z * 0.55), ts)
        energy_in = front + 0.45 * back

        fresnel = (1 - z) ** 3  # el vidrio se ve más en el borde
        lx, ly, lz = -0.45, -0.6, 0.66  # luz arriba a la izquierda
        ndl = np.clip(nx * lx + ny * ly + z * lz, 0, 1)
        hz = lz + 1
        hn = math.sqrt(lx * lx + ly * ly + hz * hz)
        ndh = np.clip((nx * lx + ny * ly + z * hz) / hn, 0, 1)
        spec = _smoothstep(0.985, 0.995, ndh) * 0.85 + ndh ** 120 * 0.4  # reflejo nítido de la luz
        soft = ndh ** 10 * 0.14
        # Reflejo inferior tenue (luz rebotada), típico del vidrio.
        bounce = _smoothstep(0.3, 0.9, ny) * fresnel * 0.3
        core = np.exp(-rr * rr * 2.5) * 0.25  # resplandor central tenue

        lum = (0.05 + 0.55 * fresnel + core + (0.8 + 0.5 * level) * energy_in * (0.55 + 0.45 * ndl)) * inside
        base, hot = self.base, self.hot
        col = (base * np.clip(0.15 + lum, 0, 1)[..., None]
               + (hot - base) * np.clip((lum - 0.7) * 1.5, 0, 1)[..., None])
        white = np.clip(spec + soft + bounce, 0, 1) * inside
        col = col + (1 - col) * white[..., None]

        halo = np.exp(-np.clip(self.r / edge - 1, 0, None) * 9) * (1 - inside) * (0.35 + 0.35 * level)
        # Semitransparente: el centro deja ver lo de atrás; borde, energía y reflejos son opacos.
        a_in = np.clip(0.16 + 0.75 * fresnel + 0.7 * energy_in + white, 0, 0.95) * inside
        alpha = np.clip(a_in + halo, 0, 1)
        col = col * inside[..., None] + base * (1 - inside)[..., None]

        out = np.empty((self.size, self.size, 4), dtype=np.uint8)
        pm = np.clip(col, 0, 1) * alpha[..., None] * 255
        out[..., 0] = pm[..., 2]
        out[..., 1] = pm[..., 1]
        out[..., 2] = pm[..., 0]
        out[..., 3] = alpha * 255
        return out


# ------------------------------------------------------------------ ventana por capas

class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_byte)]


_user32, _gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
# Los handles son de 64 bits: sin argtypes/restype ctypes los trunca a int de 32.
_gdi32.CreateDIBSection.restype = ctypes.c_void_p
_gdi32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p),
                                    ctypes.c_void_p, wt.DWORD]
_gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
_gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
_gdi32.SelectObject.restype = ctypes.c_void_p
_gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.GetDC.restype = ctypes.c_void_p
_user32.GetDC.argtypes = [wt.HWND]
_gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
_user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
_user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_user32.UpdateLayeredWindow.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE),
                                        ctypes.c_void_p, ctypes.POINTER(wt.POINT), wt.DWORD,
                                        ctypes.POINTER(_BLENDFUNCTION), wt.DWORD]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]

GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000, 0x20, 0x80, 0x08000000


class LayeredWindow:
    """Ventana sin bordes, siempre encima, que deja pasar los clics y muestra una imagen BGRA.

    Es una ventana Win32 propia y no un Toplevel de Tk: Tk rehace el estilo de sus ventanas
    al mapearlas y UpdateLayeredWindow empezaba a fallar (error 87). Se crea en el hilo de Tk,
    así que el bucle de mensajes de Tk también la atiende."""

    _class_registered = False

    def __init__(self, root: tk.Misc, size: int, click_through: bool = True):
        import win32api
        import win32con
        import win32gui
        self.size = size
        if not LayeredWindow._class_registered:
            wc = win32gui.WNDCLASS()
            wc.lpszClassName = "EcoOrb"
            wc.lpfnWndProc = lambda h, m, w, l: win32gui.DefWindowProc(h, m, w, l)
            wc.hInstance = win32api.GetModuleHandle(None)
            win32gui.RegisterClass(wc)
            LayeredWindow._class_registered = True
        ex = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | win32con.WS_EX_TOPMOST
        if click_through:
            ex |= WS_EX_TRANSPARENT
        # Creada "consciente de DPI": con escala 125-150 % Windows no la estira (texto y orbe nítidos);
        # sus coordenadas y tamaño van en píxeles reales.
        prev = _user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        try:
            self.hwnd = win32gui.CreateWindowEx(ex, "EcoOrb", "Kalmiya", win32con.WS_POPUP, 0, 0, size, size,
                                                0, 0, win32api.GetModuleHandle(None), None)
        finally:
            if prev:
                _user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(prev))
        self._alloc(size, size)

    @staticmethod
    def screen_scale() -> float:
        """Escala de la pantalla (1.5 a 150 %)."""
        # Un proceso no consciente de DPI siempre "ve" 96; se pregunta desde un contexto consciente.
        prev = _user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        try:
            return _user32.GetDpiForSystem() / 96
        except Exception:
            return 1.0
        finally:
            if prev:
                _user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(prev))

    def _alloc(self, w: int, h: int) -> None:
        self.w, self.h = w, h
        bmi = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        if not hasattr(self, "_screen_dc"):
            self._screen_dc = _user32.GetDC(None)
            self._mem_dc = _gdi32.CreateCompatibleDC(self._screen_dc)
        else:
            _gdi32.DeleteObject(ctypes.c_void_p(self._bmp))
        self._bits = ctypes.c_void_p()
        self._bmp = _gdi32.CreateDIBSection(self._mem_dc, ctypes.cast(ctypes.byref(bmi), ctypes.c_void_p), 0,
                                            ctypes.byref(self._bits), None, 0)
        _gdi32.SelectObject(self._mem_dc, self._bmp)
        self.pos = (0, 0)

    def show(self, bgra: np.ndarray, x: int, y: int) -> None:
        """`bgra`: (alto, ancho, 4) premultiplicado; `x, y` en píxeles reales de pantalla."""
        h, w = bgra.shape[:2]
        if (w, h) != (self.w, self.h):
            self._alloc(w, h)
        bgra = np.ascontiguousarray(bgra)
        ctypes.memmove(self._bits, bgra.ctypes.data, bgra.nbytes)
        blend = _BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, alfa por píxel
        _user32.UpdateLayeredWindow(self.hwnd, self._screen_dc, ctypes.byref(wt.POINT(x, y)),
                                    ctypes.byref(wt.SIZE(w, h)), self._mem_dc,
                                    ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(blend), 2)  # ULW_ALPHA
        self.pos = (x, y)
        if not _user32.IsWindowVisible(self.hwnd):
            _user32.ShowWindow(self.hwnd, 8)  # SW_SHOWNA (sin robar el foco)

    def hide(self) -> None:
        _user32.ShowWindow(self.hwnd, 0)  # SW_HIDE


class Orb:
    """Orbe flotante en el centro derecho de la pantalla, animado a ~30 fps mientras es visible."""

    SIZE = 200  # en puntos; en píxeles reales se multiplica por la escala de pantalla

    def __init__(self, root: tk.Misc, color: str = DEFAULT_COLOR, level_fn=lambda: 0.0):
        self.root = root
        self.level_fn = level_fn
        self.scale = LayeredWindow.screen_scale()
        self.px = int(self.SIZE * self.scale)
        self.renderer = OrbRenderer(self.px, color)
        self.win = LayeredWindow(root, self.px)
        self.win.hide()
        self.mode = "idle"  # listening | thinking | speaking | idle
        self.visible = False
        self._level = 0.0
        self._t0 = time.time()
        self._fade = 0.0

    def set_color(self, color: str) -> None:
        self.renderer.set_color(color)

    @property
    def position(self) -> tuple[int, int]:
        """Esquina del orbe en píxeles reales: centro derecho de la pantalla."""
        # El proceso es consciente de DPI (kalmiya.dpi): Tk ya da píxeles reales.
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        return sw - self.px - int(40 * self.scale), (sh - self.px) // 2

    def show(self, mode: str) -> None:
        self.mode = mode
        if not self.visible:
            self.visible = True
            self._tick()

    def hide(self) -> None:
        self.visible = False
        self.win.hide()
        self._fade = 0.0

    def _tick(self) -> None:
        if not self.visible:
            return
        t = time.time() - self._t0
        if self.mode == "listening":
            target = min(1.0, self.level_fn() * 14)
        elif self.mode == "speaking":
            target = 0.35 + 0.25 * math.sin(t * 9) * math.sin(t * 2.3)
        else:
            target = 0.0
        self._level += (target - self._level) * 0.35  # suavizado
        energy = {"thinking": 2.6, "listening": 1.4, "speaking": 1.6}.get(self.mode, 1.0)
        frame = self.renderer.frame(t, self._level, energy)
        self._fade = min(1.0, self._fade + 0.15)  # aparece suavemente
        if self._fade < 1:
            frame = (frame.astype(np.float32) * self._fade).astype(np.uint8)
        x, y = self.position
        self.win.show(frame, x, y)
        self.root.after(40, self._tick)
