"""Escala de pantalla en Linux.

Las ventanas de Tk (configuración, calendario…) van por XWayland y Hyprland ya las escala,
así que las medidas se dejan en puntos salvo que se fuerce con KALMIYA_SCALE.
"""
import os

_scale: float | None = None


def enable() -> None:
    pass


def scale() -> float:
    global _scale
    if _scale is None:
        try:
            _scale = float(os.environ.get("KALMIYA_SCALE", "1"))
        except ValueError:
            _scale = 1.0
    return _scale


def px(v: float) -> int:
    return int(round(v * scale()))
