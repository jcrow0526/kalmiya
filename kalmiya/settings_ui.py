"""Ventana de configuración: menú lateral, tarjetas y controles propios en tema oscuro."""
import sys
import threading
import time
import tkinter as tk
from tkinter import colorchooser
from typing import Callable

import numpy as np
from PIL import Image, ImageTk

from .config import LOG_DIR, Config
from .dpi import px
from .i18n import VOICES
from .orb import OrbRenderer

BG, SIDE, CARD, FIELD, LINE = "#0f1013", "#14151a", "#1a1b21", "#23252d", "#2c2e37"
FG, MUTED, DIM = "#f2f3f6", "#9a9ca8", "#6b6d78"
FONT = "Segoe UI" if sys.platform == "win32" else "Noto Sans"
COLORES = {"Lima": "#7CFF2B", "Cian": "#2BD9FF", "Azul": "#4C7DFF", "Violeta": "#B04CFF",
           "Rosa": "#FF4CA8", "Ámbar": "#FFB02B"}

SECTIONS = [("general", "General", "Atajos, ciudad y comportamiento"),
            ("voz", "Voz e idioma", "Cómo te escucha y cómo te habla"),
            ("apariencia", "Apariencia", "El color del orbe"),
            ("calendario", "Calendario", "Eventos, recordatorios y Google Calendar"),
            ("noticias", "Noticias", "Tus periódicos para «resúmeme las noticias»"),
            ("avanzado", "Avanzado", "Modelos y servidor")]


class SettingsWindow:
    def __init__(self, root: tk.Misc, cfg: Config, *, on_save: Callable[[], None],
                 preview_color: Callable[[str], None], preview_voice: Callable[[str, str], None],
                 open_calendar: Callable[[], None], open_reminders: Callable[[], None],
                 connect_google: Callable[[], None], google_connected: Callable[[], bool]):
        self.cfg = cfg
        self.on_save, self.preview_color, self.preview_voice = on_save, preview_color, preview_voice
        self.open_calendar, self.open_reminders = open_calendar, open_reminders
        self.connect_google, self.google_connected = connect_google, google_connected
        # Copia de trabajo: nada se aplica hasta "Guardar".
        self.v = {k: getattr(cfg, k) for k in vars(cfg)}
        self.v["noticias"] = [dict(f) for f in cfg.noticias]
        self.accent = cfg.color_orbe

        w = self.win = tk.Toplevel(root)
        w.title("Kalmiya — Configuración")
        w.configure(bg=BG)
        W, H = px(940), px(640)
        w.geometry(f"{W}x{H}+{(w.winfo_screenwidth() - W) // 2}+{(w.winfo_screenheight() - H) // 2}")
        w.minsize(px(820), px(560))
        from .welcome import _dark_titlebar, set_icon
        _dark_titlebar(w)
        set_icon(w, cfg.color_orbe)
        w.protocol("WM_DELETE_WINDOW", self.cancel)

        side = tk.Frame(w, bg=SIDE, width=px(230))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        head = tk.Frame(side, bg=SIDE, padx=px(20), pady=px(22))
        head.pack(fill="x")
        self._side_orb = tk.Label(head, bg=SIDE)
        self._side_orb.pack(side="left")
        tk.Label(head, text="Kalmiya", bg=SIDE, fg=FG, font=(FONT, 15, "bold")).pack(side="left", padx=(px(10), 0))
        self.nav = {}
        for key, title, _ in SECTIONS:
            item = tk.Frame(side, bg=SIDE, cursor="hand2")
            item.pack(fill="x", padx=px(10), pady=px(1))
            bar = tk.Frame(item, bg=SIDE, width=px(3))
            bar.pack(side="left", fill="y")
            lbl = tk.Label(item, text=title, bg=SIDE, fg=MUTED, font=(FONT, 11), anchor="w", padx=px(14), pady=px(9))
            lbl.pack(side="left", fill="x", expand=True)
            for wd in (item, lbl):
                wd.bind("<Button-1>", lambda e, k=key: self.show(k))
            self.nav[key] = (item, bar, lbl)

        foot = tk.Frame(w, bg=BG, padx=px(28), pady=px(16))
        foot.pack(side="bottom", fill="x")
        tk.Frame(w, bg=LINE, height=1).pack(side="bottom", fill="x")
        self._button(foot, "Guardar", self.save, primary=True).pack(side="right")
        self._button(foot, "Cancelar", self.cancel).pack(side="right", padx=px(8))
        self.status = tk.Label(foot, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.status.pack(side="left")

        # Contenido con desplazamiento.
        holder = tk.Frame(w, bg=BG)
        holder.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(holder, bg=BG, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=BG, padx=px(32), pady=px(24))
        self._body_id = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._body_id, width=e.width))
        w.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

        self._renderer = OrbRenderer(px(30), self.accent)
        self._t0 = time.time()
        self._preview = None
        self._animate()
        self.show("general")

    # ------------------------------------------------------------ piezas
    def _button(self, parent, text, cmd, primary=False, small=False):
        bg = self.accent if primary else FIELD
        b = tk.Label(parent, text=text, bg=bg, fg="#0b0c0e" if primary else FG, cursor="hand2",
                     font=(FONT, 9 if small else 10, "bold" if primary else "normal"),
                     padx=px(10 if small else 16), pady=px(4 if small else 7))
        b.bind("<Button-1>", lambda e: cmd())
        if not primary:
            b.bind("<Enter>", lambda e: b.config(bg=LINE))
            b.bind("<Leave>", lambda e: b.config(bg=FIELD))
        return b

    def _section(self, title: str, desc: str) -> None:
        for c in self.body.winfo_children():
            c.destroy()
        tk.Label(self.body, text=title, bg=BG, fg=FG, font=(FONT, 20, "bold"), anchor="w").pack(fill="x")
        tk.Label(self.body, text=desc, bg=BG, fg=MUTED, font=(FONT, 10), anchor="w").pack(fill="x", pady=(0, px(14)))

    def _card(self, title: str | None = None) -> tk.Frame:
        if title:
            tk.Label(self.body, text=title.upper(), bg=BG, fg=DIM, font=(FONT, 8, "bold"), anchor="w").pack(
                fill="x", pady=(px(10), px(6)))
        card = tk.Frame(self.body, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        card.pack(fill="x")
        return card

    def _row(self, card, label: str, desc: str = "") -> tk.Frame:
        if card.winfo_children():
            tk.Frame(card, bg=LINE, height=1).pack(fill="x", padx=px(16))
        row = tk.Frame(card, bg=CARD, padx=px(16), pady=px(11))
        row.pack(fill="x")
        txt = tk.Frame(row, bg=CARD)
        txt.pack(side="left", fill="x", expand=True)
        tk.Label(txt, text=label, bg=CARD, fg=FG, font=(FONT, 10), anchor="w").pack(fill="x")
        if desc:
            tk.Label(txt, text=desc, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w", justify="left",
                     wraplength=px(380)).pack(fill="x")
        ctl = tk.Frame(row, bg=CARD)
        ctl.pack(side="right")
        return ctl

    def _toggle(self, parent, key: str) -> None:
        c = tk.Canvas(parent, width=px(42), height=px(24), bg=CARD, highlightthickness=0, cursor="hand2")
        c.pack()

        def draw():
            c.delete("all")
            on = bool(self.v[key])
            w, h, r = px(42), px(24), px(12)
            fill = self.accent if on else "#3a3c46"
            c.create_oval(0, 0, h, h, fill=fill, width=0)
            c.create_oval(w - h, 0, w, h, fill=fill, width=0)
            c.create_rectangle(r, 0, w - r, h, fill=fill, width=0)
            x = w - h + px(3) if on else px(3)
            c.create_oval(x, px(3), x + h - px(6), h - px(3), fill="#0b0c0e" if on else "#d8d9de", width=0)

        def flip(_):
            self.v[key] = not self.v[key]
            draw()
        c.bind("<Button-1>", flip)
        draw()

    def _segmented(self, parent, key: str, options: list[tuple[str, str]], on_change=None) -> None:
        box = tk.Frame(parent, bg=FIELD, padx=px(3), pady=px(3))
        box.pack()
        labels = []

        def draw():
            for (val, _), lbl in zip(options, labels):
                sel = self.v[key] == val
                lbl.config(bg=self.accent if sel else FIELD, fg="#0b0c0e" if sel else FG)

        for val, text in options:
            lbl = tk.Label(box, text=text, font=(FONT, 9, "bold"), padx=px(12), pady=px(4), cursor="hand2")
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda e, v=val: (self.v.__setitem__(key, v), draw(), on_change and on_change()))
            labels.append(lbl)
        draw()

    def _entry(self, parent, key: str, width: int = 24) -> tk.Entry:
        var = tk.StringVar(value=str(self.v[key] or ""))
        var.trace_add("write", lambda *a: self.v.__setitem__(key, var.get()))
        e = tk.Entry(parent, textvariable=var, width=width, bg=FIELD, fg=FG, insertbackground=FG, relief="flat",
                     font=(FONT, 10), highlightthickness=1, highlightbackground=LINE, highlightcolor=self.accent)
        e.pack(side="left", ipady=px(4))
        e.var = var
        return e

    def _dropdown(self, parent, key: str, options: list[tuple[str, str]], on_change=None) -> None:
        names = dict(options)
        mb = tk.Menubutton(parent, text=f"{names.get(self.v[key], self.v[key])}  ▾", bg=FIELD, fg=FG,
                           activebackground=LINE, activeforeground=FG, relief="flat", font=(FONT, 10),
                           padx=px(12), pady=px(5), cursor="hand2", indicatoron=False)

        def pick(val):
            self.v[key] = val
            mb.config(text=f"{names[val]}  ▾")
            if on_change:
                on_change()
        menu = tk.Menu(mb, tearoff=False, bg=FIELD, fg=FG, activebackground=self.accent, activeforeground="#0b0c0e",
                       font=(FONT, 10), bd=0)
        for val, text in options:
            menu.add_command(label=text, command=lambda v=val: pick(v))
        mb.config(menu=menu)
        mb.pack(side="left")

    def _hotkey(self, parent, key: str) -> None:
        e = self._entry(parent, key, width=18)

        if sys.platform != "win32":
            # En Wayland no se puede leer el teclado global: se escribe ("ctrl+alt+space") y,
            # al guardar, Kalmiya lo registra en Hyprland (~/.config/hypr/kalmiya.lua).
            return

        def record():
            self.status.config(text="Pulsa la combinación de teclas…")
            btn.config(text="…")

            def run():
                import keyboard
                combo = keyboard.read_hotkey(suppress=False)
                self.win.after(0, lambda: (e.var.set(combo), btn.config(text="Grabar"),
                                           self.status.config(text=f"Atajo: {combo}")))
            threading.Thread(target=run, daemon=True).start()
        btn = self._button(parent, "Grabar", record, small=True)
        btn.pack(side="left", padx=(px(8), 0))

    def _animate(self) -> None:
        if not self.win.winfo_exists():
            return
        t = time.time() - self._t0
        imgs = [(self._side_orb, self._renderer, SIDE)]
        if self._preview is not None and self._preview[0].winfo_exists():
            imgs.append(self._preview)
        for lbl, rend, bg in imgs:
            f = rend.frame(t, 0.25).astype(np.float32) / 255
            a = f[..., 3:4]
            b = np.array([int(bg[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255
            rgb = f[..., [2, 1, 0]] + b * (1 - a)
            lbl._img = ImageTk.PhotoImage(Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)))
            lbl.config(image=lbl._img)
        self.win.after(50, self._animate)

    # ------------------------------------------------------------ secciones
    def show(self, key: str) -> None:
        for k, (item, bar, lbl) in self.nav.items():
            sel = k == key
            item.config(bg=FIELD if sel else SIDE)
            lbl.config(bg=FIELD if sel else SIDE, fg=FG if sel else MUTED)
            bar.config(bg=self.accent if sel else SIDE)
        title, desc = next((t, d) for k, t, d in SECTIONS if k == key)
        self._section(title, desc)
        self._preview = None
        getattr(self, f"_s_{key}")()
        self.canvas.yview_moveto(0)

    def _s_general(self) -> None:
        c = self._card("Atajos")
        self._hotkey(self._row(c, "Hablar con Kalmiya", "Mantén, habla y suelta"), "hotkey_agente")
        self._hotkey(self._row(c, "Dictado", "Escribe lo que digas donde esté el cursor"), "hotkey_dictado")
        self._hotkey(self._row(c, "Nueva conversación", "Olvida lo anterior"), "hotkey_nueva")
        self._hotkey(self._row(c, "Preguntar por una imagen", "Luego haz clic en la imagen"), "hotkey_imagen")
        self._segmented(self._row(c, "Modo del atajo", "Mantener pulsado o pulsar una vez para empezar y otra para terminar"),
                        "modo_hotkey", [("mantener", "Mantener"), ("alternar", "Alternar")])
        c = self._card("Clima")
        ctl = self._row(c, "Tu ciudad", "Se detecta sola por tu conexión; puedes escribir otra")
        e = self._entry(ctl, "ciudad", width=20)

        def detect():
            self.status.config(text="Detectando tu ciudad…")

            def run():
                from .geo import detect_city
                city = detect_city()
                self.win.after(0, lambda: (e.var.set(city) if city else None,
                                           self.status.config(text=f"Ciudad: {city}" if city else "No pude detectarla")))
            threading.Thread(target=run, daemon=True).start()
        self._button(ctl, "Detectar", detect, small=True).pack(side="left", padx=(px(8), 0))
        c = self._card("Comportamiento")
        self._toggle(self._row(c, "Preguntar si no está segura",
                               "Si entendió mal o el resultado no se parece, pregunta antes de actuar"), "confirmar_si_dudo")
        self._toggle(self._row(c, "Escuchar tu respuesta sin atajo", "Tras una pregunta, contesta «sí» o «no» directamente"),
                     "escuchar_respuesta")
        self._toggle(self._row(c, "Bajar el volumen al hablar", "Las demás apps bajan mientras le hablas"), "bajar_volumen")
        self._toggle(self._row(c, "Responder con voz"), "responder_con_voz")

    def _s_voz(self) -> None:
        c = self._card("Idioma")

        def lang_changed():
            from .i18n import DEFAULT_VOICE
            lang = "en" if self.v["idioma"].startswith("en") else "es"
            if self.v["piper_voz"] not in VOICES[lang]:
                self.v["piper_voz"] = DEFAULT_VOICE[lang]
            self.show("voz")
        self._segmented(self._row(c, "Idioma", "Textos, reconocimiento de voz y respuestas"), "idioma",
                        [("es", "Español"), ("en", "English")], lang_changed)
        c = self._card("Voz")
        lang = "en" if self.v["idioma"].startswith("en") else "es"
        for voice, label in VOICES[lang].items():
            ctl = self._row(c, label.split(" — ")[0], label.split(" — ")[1] if " — " in label else "")
            sel = self.v["piper_voz"] == voice
            self._button(ctl, "▶ Escuchar", lambda v=voice: self.preview_voice(
                v, "Hola, soy Kalmiya. ¿En qué te ayudo?" if lang == "es" else "Hi, I'm Kalmiya. How can I help?"),
                small=True).pack(side="left", padx=(0, px(8)))
            self._button(ctl, "Elegida" if sel else "Elegir",
                         lambda v=voice: (self.v.__setitem__("piper_voz", v), self.show("voz")),
                         primary=sel, small=True).pack(side="left")
        self._segmented(self._row(c, "Velocidad"), "velocidad_voz", [(0.9, "Lenta"), (1.0, "Normal"), (1.15, "Rápida")])

    def _s_apariencia(self) -> None:
        c = self._card("Orbe")
        row = tk.Frame(c, bg=CARD, padx=px(16), pady=px(16))
        row.pack(fill="x")
        prev = tk.Label(row, bg=CARD)
        prev.pack(side="left")
        self._prev_renderer = OrbRenderer(px(150), self.v["color_orbe"])
        self._preview = (prev, self._prev_renderer, CARD)
        right = tk.Frame(row, bg=CARD, padx=px(20))
        right.pack(side="left", fill="x", expand=True)
        tk.Label(right, text="Color", bg=CARD, fg=FG, font=(FONT, 11, "bold"), anchor="w").pack(fill="x")
        tk.Label(right, text="Se aplica al orbe, a los botones y al icono", bg=CARD, fg=MUTED, font=(FONT, 9),
                 anchor="w").pack(fill="x", pady=(0, px(10)))
        sw = tk.Frame(right, bg=CARD)
        sw.pack(anchor="w")

        def pick(col):
            self.v["color_orbe"] = col
            self._prev_renderer.set_color(col)
            self.preview_color(col)
            self.show("apariencia")
        for name, col in COLORES.items():
            sel = col.lower() == str(self.v["color_orbe"]).lower()
            cell = tk.Frame(sw, bg=CARD)
            cell.pack(side="left", padx=px(4))
            b = tk.Label(cell, bg=col, width=4, height=2, cursor="hand2", highlightthickness=2 if sel else 0,
                         highlightbackground=FG)
            b.pack()
            tk.Label(cell, text=name, bg=CARD, fg=FG if sel else MUTED, font=(FONT, 8)).pack()
            b.bind("<Button-1>", lambda e, c_=col: pick(c_))
        self._button(sw, "Otro…", lambda: (lambda c_: c_ and pick(c_))(
            colorchooser.askcolor(self.v["color_orbe"], parent=self.win)[1]), small=True).pack(side="left", padx=px(8))

    def _s_calendario(self) -> None:
        c = self._card("Dónde se guardan los eventos")
        self._segmented(self._row(c, "Calendario", "Kalmiya guarda en su calendario (y en Google si lo conectas)"),
                        "calendario", [("kalmiya", "Kalmiya"), ("outlook", "Outlook"), ("ics", "Archivo .ics")])
        c = self._card("Google Calendar")
        ok = self.google_connected()
        ctl = self._row(c, "Conectado" if ok else "Sin conectar",
                        "Lo que agendes con la voz también aparece en tu teléfono" if ok
                        else "Conéctalo para ver tus eventos de Google y guardar ahí los nuevos")
        self._button(ctl, "Administrar" if ok else "Conectar", self.connect_google, primary=not ok, small=True).pack()
        c = self._card("Abrir")
        self._button(self._row(c, "Calendario", "Vista de mes, eventos y recordatorios"), "Abrir",
                     self.open_calendar, small=True).pack()
        self._button(self._row(c, "Recordatorios", "Todos tus recordatorios pendientes"), "Abrir",
                     self.open_reminders, small=True).pack()

    def _s_noticias(self) -> None:
        from . import news
        mine = self.v["noticias"]
        c = self._card("Tus periódicos")
        if not mine:
            tk.Label(c, text="Aún no tienes periódicos. Añade alguno de los recomendados o pega el enlace de uno.",
                     bg=CARD, fg=MUTED, font=(FONT, 10), padx=px(16), pady=px(14), anchor="w").pack(fill="x")
        for f in mine:
            ctl = self._row(c, f["nombre"], f["url"][:70])
            self._button(ctl, "Quitar", lambda f=f: (mine.remove(f), self.show("noticias")), small=True).pack()

        c = self._card("Añadir por enlace")
        ctl = self._row(c, "Página o feed RSS", "Pega la dirección del periódico; Kalmiya busca su RSS")
        var = tk.StringVar()
        e = tk.Entry(ctl, textvariable=var, width=26, bg=FIELD, fg=FG, insertbackground=FG, relief="flat",
                     font=(FONT, 10), highlightthickness=1, highlightbackground=LINE, highlightcolor=self.accent)
        e.pack(side="left", ipady=px(4))

        def add_url():
            url = var.get().strip()
            if not url:
                return
            self.status.config(text="Buscando el RSS…")

            def run():
                try:
                    name, feed = news.discover(url)
                    items = news.fetch(name, feed)
                    def done():
                        mine.append({"nombre": name, "url": feed})
                        self.status.config(text=f"Añadido {name} ({len(items)} noticias)")
                        self.show("noticias")
                    self.win.after(0, done)
                except Exception as ex:
                    self.win.after(0, lambda: self.status.config(text=f"No pude añadirlo: {ex}"))
            threading.Thread(target=run, daemon=True).start()
        self._button(ctl, "Añadir", add_url, primary=True, small=True).pack(side="left", padx=(px(8), 0))

        lang = "en" if self.v["idioma"].startswith("en") else "es"
        have = {f["url"] for f in mine}
        cats: dict[str, list] = {}
        for name, url, cat, flang in sorted(news.CATALOG, key=lambda x: x[3] != lang):
            if url not in have:
                cats.setdefault(cat, []).append((name, url))
        for cat, feeds in cats.items():
            c = self._card(f"Recomendados · {cat}")
            for name, url in feeds:
                ctl = self._row(c, name)
                self._button(ctl, "+ Añadir", lambda n=name, u=url: (mine.append({"nombre": n, "url": u}),
                                                                      self.show("noticias")), small=True).pack()

    def _s_avanzado(self) -> None:
        c = self._card("Voz a texto")
        self._segmented(self._row(c, "Motor"), "stt_motor", [("whisper", "Whisper"), ("nemotron", "Nemotron")])
        self._dropdown(self._row(c, "Modelo Whisper", "large-v3-turbo es el más preciso; small, el más ligero"),
                       "whisper_modelo", [(m, m) for m in ("tiny", "base", "small", "medium", "large-v3-turbo")])
        self._entry(self._row(c, "Modelo Nemotron", "Alias (nemotron-3.5) o archivo GGUF"), "nemo_modelo")
        c = self._card("Modelo de lenguaje")
        self._entry(self._row(c, "Servidor LLM externo", "Vacío = LFM3 local. Ej.: http://localhost:11434/v1"),
                    "llm_url_externa", width=30)
        c = self._card("Diagnóstico")
        import os
        self._button(self._row(c, "Registros", "Lo que Kalmiya oyó e hizo"), "Abrir carpeta",
                     lambda: (os.startfile(str(LOG_DIR)) if sys.platform == "win32"
                              else __import__("subprocess").Popen(["xdg-open", str(LOG_DIR)])), small=True).pack()

    # ------------------------------------------------------------ guardar
    def save(self) -> None:
        for k, val in self.v.items():
            setattr(self.cfg, k, val)
        self.cfg.save()
        self.win.destroy()
        self.on_save()

    def cancel(self) -> None:
        self.preview_color(self.cfg.color_orbe)  # deshacer la vista previa
        self.win.destroy()
