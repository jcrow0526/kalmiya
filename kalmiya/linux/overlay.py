"""Orbe y burbuja de vidrio en Wayland: un proceso aparte con GTK 4 y gtk4-layer-shell.

Tk no tiene transparencia por píxel en Wayland, así que el orbe y la burbuja se dibujan aquí
con el mismo código que en Windows (OrbRenderer, GlassBubble) y se muestran como capas
"overlay" que dejan pasar los clics y no roban el foco.

La app principal lo arranca (`python -m kalmiya.linux.overlay`, con LD_PRELOAD de
libgtk4-layer-shell) y le manda órdenes JSON por stdin, una por línea:
  {"op": "status", "title": …, "body": …, "mode": "listening|thinking|speaking"}
  {"op": "hide"}  {"op": "color", "color": "#7CFF2B"}  {"op": "level", "value": 0.03}  {"op": "quit"}
"""
import json
import logging
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
import cairo  # noqa: E402
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as Layer  # noqa: E402

from .. import glass, orb  # noqa: E402
from . import desktop  # noqa: E402

log = logging.getLogger("kalmiya.overlay")


class _Screen:
    """Monitor con el foco: nombre, posición y tamaño lógicos, y escala."""

    def __init__(self):
        self.refresh()

    def refresh(self) -> None:
        m = desktop.focused_monitor()
        self.name = m.get("name", "")
        self.scale = float(m.get("scale", 1.0)) or 1.0
        # hyprctl da el tamaño en píxeles reales; las capas se colocan en puntos lógicos.
        self.width_px, self.height_px = int(m.get("width", 1920)), int(m.get("height", 1080))
        self.shot = None  # captura del monitor para el vidrio esmerilado

    def gdk_monitor(self):
        mons = Gdk.Display.get_default().get_monitors()
        for i in range(mons.get_n_items()):
            mon = mons.get_item(i)
            if mon.get_connector() == self.name:
                return mon
        return None


SCREEN: _Screen | None = None


class LayerWindow:
    """Lo mismo que orb.LayeredWindow (Windows): imagen BGRA premultiplicada en (x, y) de pantalla,
    siempre encima y sin recibir clics."""

    def __init__(self, root, size: int, click_through: bool = True):
        self.size = size
        w = self.win = Gtk.Window()
        w.set_decorated(False)
        w.add_css_class("kalmiya")
        Layer.init_for_window(w)
        Layer.set_namespace(w, "kalmiya")
        Layer.set_layer(w, Layer.Layer.OVERLAY)
        Layer.set_keyboard_mode(w, Layer.KeyboardMode.NONE)
        Layer.set_exclusive_zone(w, -1)  # coordenadas desde el borde del monitor, ignorando la barra
        Layer.set_anchor(w, Layer.Edge.LEFT, True)
        Layer.set_anchor(w, Layer.Edge.TOP, True)
        self.pic = Gtk.Picture()
        self.pic.set_can_shrink(True)
        w.set_child(self.pic)
        w.connect("realize", self._no_input)
        self.pos = (0, 0)
        self.w = self.h = 0
        self._monitor = None

    @staticmethod
    def screen_scale() -> float:
        return SCREEN.scale if SCREEN else 1.0

    @staticmethod
    def _no_input(win) -> None:
        surface = win.get_surface()
        if surface is not None:
            surface.set_input_region(cairo.Region())

    def show(self, bgra, x: int, y: int) -> None:
        h, w = bgra.shape[:2]
        s = SCREEN.scale
        tex = Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                    GLib.Bytes.new(bgra.tobytes()), w * 4)
        self.pic.set_paintable(tex)
        lw, lh = max(1, round(w / s)), max(1, round(h / s))
        if (lw, lh) != (self.w, self.h):
            self.w, self.h = lw, lh
            self.pic.set_size_request(lw, lh)
            self.win.set_default_size(lw, lh)
        lx, ly = round(x / s), round(y / s)
        if (lx, ly) != self.pos:
            Layer.set_margin(self.win, Layer.Edge.LEFT, lx)
            Layer.set_margin(self.win, Layer.Edge.TOP, ly)
            self.pos = (lx, ly)
        mon = SCREEN.gdk_monitor()
        if mon is not None and mon is not self._monitor:
            Layer.set_monitor(self.win, mon)
            self._monitor = mon
        if not self.win.get_visible():
            self.win.present()
            self._no_input(self.win)

    def hide(self) -> None:
        self.win.set_visible(False)


class _Root:
    """Lo que Orb y GlassBubble usan de Tk."""

    @staticmethod
    def after(ms: int, fn) -> None:
        GLib.timeout_add(ms, lambda: (fn(), False)[1])

    @staticmethod
    def winfo_screenwidth() -> int:
        return SCREEN.width_px

    @staticmethod
    def winfo_screenheight() -> int:
        return SCREEN.height_px


orb.LayeredWindow = LayerWindow
glass.LayeredWindow = LayerWindow


class Bubble(glass.GlassBubble):
    def _background(self, bbox):
        # La captura se toma una vez, al aparecer el orbe y antes de mostrarlo: en Wayland grim
        # también vería el propio orbe y la burbuja anterior. Sin captura, vidrio liso.
        if SCREEN.shot is None:
            raise RuntimeError("sin captura")
        return SCREEN.shot.crop(bbox)


class Overlay:
    def __init__(self, color: str):
        self.level = 0.0
        self.orb = orb.Orb(_Root, color, lambda: self.level)
        self.bubble = Bubble(_Root, color)
        self._text: tuple[str, str] | None = None

    def handle(self, msg: dict) -> None:
        op = msg.get("op")
        if op == "level":
            self.level = float(msg.get("value", 0))
        elif op == "status":
            if not self.orb.visible:
                SCREEN.refresh()
                try:
                    SCREEN.shot = desktop.grab(output=SCREEN.name or None)
                except Exception as e:
                    log.warning("Sin captura para el vidrio: %s", e)
            self.orb.show(msg.get("mode") or "thinking")
            title, body = msg.get("title", ""), msg.get("body", "")
            if title or body:
                if self._text != (title, body) or not self.bubble.visible:
                    self._text = (title, body)
                    ox, oy = self.orb.position
                    self.bubble.render(title, body, right=ox + int(self.orb.px * 0.12),
                                       center_y=oy + self.orb.px // 2)
            else:
                self.bubble.hide()
        elif op == "hide":
            self.bubble.hide()
            self.orb.hide()
            SCREEN.shot = None
        elif op == "color":
            self.orb.set_color(msg["color"])
            self.bubble.accent = msg["color"]
        elif op == "quit":
            app.quit()


def _read_stdin(ov: Overlay) -> None:
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        GLib.idle_add(lambda m=msg: (ov.handle(m), False)[1])
    GLib.idle_add(app.quit)  # la app principal se cerró


app = Gtk.Application(application_id="org.kalmiya.Overlay", flags=Gio.ApplicationFlags.NON_UNIQUE)


def _activate(application) -> None:
    global SCREEN
    SCREEN = _Screen()
    css = Gtk.CssProvider()
    css.load_from_string("window.kalmiya { background: transparent; }")
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    ov = Overlay(sys.argv[1] if len(sys.argv) > 1 else orb.DEFAULT_COLOR)
    application.hold()  # sin ventanas visibles la app sigue viva
    threading.Thread(target=_read_stdin, args=(ov,), daemon=True).start()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app.connect("activate", _activate)
    app.run([sys.argv[0]])


if __name__ == "__main__":
    main()
