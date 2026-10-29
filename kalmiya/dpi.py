"""Escala de pantalla (125 %, 150 %…).

El proceso se declara consciente de DPI al arrancar, antes de crear Tk. Si no, la primera
captura de PIL lo cambia a mitad de ejecución y Tk queda con medidas de la escala anterior
(el velo del selector de imagen cubría solo 2/3 de la pantalla a 150 %).
Con esto todo va en píxeles reales; las medidas fijas en código se pasan por `px()`.
"""
import ctypes

_scale: float | None = None


def enable() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()


def scale() -> float:
    global _scale
    if _scale is None:
        try:
            _scale = ctypes.windll.user32.GetDpiForSystem() / 96
        except Exception:
            _scale = 1.0
    return _scale


def px(v: float) -> int:
    """Medida pensada a 100 % → píxeles reales a la escala actual."""
    return int(round(v * scale()))
