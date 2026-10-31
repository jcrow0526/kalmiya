import sys

__version__ = "0.2.0"

LINUX = sys.platform.startswith("linux")

if LINUX:
    # En Linux, los módulos que usan Win32 (ventanas, OCR, escala) se sustituyen por los de
    # kalmiya/linux; así `from . import context` funciona igual en las dos plataformas.
    from .linux import context as _context, dpi as _dpi, screen as _screen

    for _name, _mod in (("context", _context), ("dpi", _dpi), ("screen", _screen)):
        sys.modules[f"{__name__}.{_name}"] = _mod
        setattr(sys.modules[__name__], _name, _mod)
