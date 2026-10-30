"""Presentación de primer uso: idioma y voz, qué es Kalmiya, los atajos y qué se le puede pedir."""
import sys
import time
import tkinter as tk
from typing import Callable

import numpy as np
from PIL import Image, ImageTk

from .config import Config
from .dpi import px
from .i18n import DEFAULT_VOICE, VOICES, set_lang
from .orb import OrbRenderer

BG, FG, MUTED, CARD, CARD_HI = "#0d0e11", "#f2f2f5", "#8e909c", "#17181d", "#22242b"
FONT = "Segoe UI" if sys.platform == "win32" else "Noto Sans"
COLORES = {"Lima": "#7CFF2B", "Cian": "#2BD9FF", "Azul": "#4C7DFF", "Violeta": "#B04CFF",
           "Rosa": "#FF4CA8", "Ámbar": "#FFB02B"}
PREVIEW = {"es": "Hola, soy Kalmiya. ¿En qué te ayudo?", "en": "Hi, I'm Kalmiya. How can I help?"}


def pretty(hotkey: str, en: bool = False) -> str:
    return " + ".join(p.capitalize() if en else p.capitalize().replace("Space", "Espacio") for p in hotkey.split("+"))


def slides(cfg: Config) -> list[tuple[str, str, list[str]]]:
    k = lambda h: pretty(h, cfg.idioma.startswith("en"))
    if cfg.idioma.startswith("en"):
        return [
            ("Hi, I'm Kalmiya",
             "Your voice assistant for " + ("Windows" if sys.platform == "win32" else "Linux") + ". I listen, understand and act on your PC — and everything runs "
             "right here: your voice never leaves your computer.", []),
            ("Hold, talk, release",
             f"Hold {k(cfg.hotkey_agente)}, tell me what you need and let go. This orb moves with your voice "
             "and I answer out loud.", ["“Play Bad Bunny”", "“What's the weather?”", "“Open Spotify”"]),
            ("I see what you have open",
             "On YouTube, WhatsApp or a browser tab, I act on it. If I'm not sure, I ask — just say “yes” or "
             "“no”, no keys needed.", ["“Open the first recommended video”", "“Close this tab”"]),
            ("Your calendar",
             "Schedule things by voice and ask what's coming up. I remind you 10 minutes before. Connect Google "
             "Calendar from the calendar (Kalmiya icon → Calendar).",
             ["“Add dentist on Friday at 4”", "“What do I have tomorrow?”"]),
            ("Ask me about an image",
             f"Press {k(cfg.hotkey_imagen)}, click an image on screen (or drag a box) and ask anything about it.",
             ["“What is this?”", "“What does this text say?”"]),
            ("More shortcuts",
             f"{k(cfg.hotkey_dictado)}: dictation — I type what you say wherever the cursor is.\n"
             f"{k(cfg.hotkey_nueva)}: new conversation — I forget the previous context.\n\n"
             "Change everything in Settings (right-click the Kalmiya icon next to the clock).", []),
            ("Pick my color", "You can change it anytime in Settings.", []),
        ]
    return [
        ("Hola, soy Kalmiya",
         "Tu asistente de voz para " + ("Windows" if sys.platform == "win32" else "Linux") + ". Escucho, entiendo y actúo en tu PC, y todo corre aquí mismo: "
         "tu voz no sale de tu computadora.", []),
        ("Mantén, habla, suelta",
         f"Mantén {k(cfg.hotkey_agente)}, dime lo que necesitas y suelta. Este orbe se mueve con tu voz "
         "y te respondo en voz alta.", ["«Pon Bad Bunny»", "«¿Cómo está el clima?»", "«Abre Spotify»"]),
        ("Entiendo lo que tienes abierto",
         "Si estás en YouTube, en WhatsApp o en una pestaña, actúo sobre eso. Y si no estoy segura, te pregunto: "
         "contéstame «sí» o «no» sin pulsar nada.",
         ["«Abre el primer recomendado»", "«Escríbele a Ana que ya voy»", "«Cierra esta pestaña»"]),
        ("Tu calendario",
         "Agenda con la voz y pregúntame qué tienes. Te aviso 10 minutos antes. Puedes conectar Google Calendar "
         "desde el calendario (icono de Kalmiya → Calendario).",
         ["«Agéndame dentista el viernes a las 4»", "«¿Qué tengo mañana?»"]),
        ("Pregúntame por una imagen",
         f"Pulsa {k(cfg.hotkey_imagen)}, haz clic en una imagen de la pantalla (o arrastra un recuadro) "
         "y pregúntame lo que quieras de ella.", ["«¿Qué es esto?»", "«¿Qué dice este texto?»"]),
        ("Más atajos",
         f"{k(cfg.hotkey_dictado)}: dictado — escribo lo que digas donde esté el cursor.\n"
         f"{k(cfg.hotkey_nueva)}: nueva conversación — olvido lo anterior.\n\n"
         "Todo se cambia en Configuración (clic derecho en el icono de Kalmiya, junto al reloj).", []),
        ("Elige mi color", "Puedes cambiarlo cuando quieras en Configuración.", []),
    ]


class Welcome:
    def __init__(self, root: tk.Misc, cfg: Config, on_color: Callable[[str], None], on_close: Callable[[], None],
                 on_language: Callable[[], None] = lambda: None,
                 preview_voice: Callable[[str, str], None] = lambda v, t: None):
        self.cfg, self.on_color, self.on_close = cfg, on_color, on_close
        self.on_language, self.preview_voice = on_language, preview_voice
        self.i = -1  # -1 = elegir idioma
        if cfg.primera_vez:  # voz por defecto del idioma (mujer, español latino), aunque viniera otra de antes
            cfg.piper_voz = DEFAULT_VOICE["en" if cfg.idioma.startswith("en") else "es"]
            on_language()
        w = self.win = tk.Toplevel(root)
        w.title("Kalmiya")
        w.configure(bg=BG)
        w.resizable(False, False)
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        W, H = px(920), px(540)
        w.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 2}")
        self.ORB = px(300)
        w.attributes("-topmost", True)
        w.after(800, lambda: w.attributes("-topmost", False))
        w.protocol("WM_DELETE_WINDOW", self.close)
        _dark_titlebar(w)
        set_icon(w, cfg.color_orbe)

        self.renderer = OrbRenderer(self.ORB, cfg.color_orbe)
        self.canvas = tk.Canvas(w, width=self.ORB + px(40), height=H, bg=BG, highlightthickness=0)
        self.canvas.pack(side="left")
        self._img = None
        self._t0 = time.time()

        right = tk.Frame(w, bg=BG, padx=px(36), pady=px(48))
        right.pack(side="left", fill="both", expand=True)
        self.step = tk.Label(right, bg=BG, fg=MUTED, font=(FONT, 10), anchor="w")
        self.step.pack(fill="x")
        self.title = tk.Label(right, bg=BG, fg=FG, font=(FONT, 24, "bold"), anchor="w", justify="left",
                              wraplength=px(480))
        self.title.pack(fill="x", pady=(6, 12))
        self.body = tk.Label(right, bg=BG, fg="#c9cad3", font=(FONT, 12), anchor="w", justify="left", wraplength=px(480))
        self.body.pack(fill="x")
        self.extra = tk.Frame(right, bg=BG)
        self.extra.pack(fill="x", pady=(18, 0))

        nav = tk.Frame(right, bg=BG)
        nav.pack(side="bottom", fill="x")
        self.dots = tk.Frame(nav, bg=BG)
        self.dots.pack(side="left")
        self.next_btn = self._btn(nav, "", self.next, primary=True)
        self.next_btn.pack(side="right")
        self.back_btn = self._btn(nav, "", self.back)
        self.back_btn.pack(side="right", padx=8)
        w.bind("<Right>", lambda e: self.next())
        w.bind("<Return>", lambda e: self.next())
        w.bind("<Left>", lambda e: self.back())
        self.render()
        self._animate()

    @property
    def en(self) -> bool:
        return self.cfg.idioma.startswith("en")

    def _btn(self, parent, text, cmd, primary=False):
        b = tk.Label(parent, text=text, font=(FONT, 11, "bold" if primary else "normal"), padx=18, pady=8,
                     cursor="hand2", bg=self.cfg.color_orbe if primary else CARD, fg="#0b0c0e" if primary else FG)
        b.bind("<Button-1>", lambda e: cmd())
        return b

    def _animate(self) -> None:
        if not self.win.winfo_exists():
            return
        t = time.time() - self._t0
        level = 0.25 + 0.2 * np.sin(t * 1.7)
        f = self.renderer.frame(t, float(level)).astype(np.float32) / 255
        a = f[..., 3:4]
        bg = np.array([13, 14, 17], np.float32) / 255
        rgb = f[..., [2, 1, 0]] + bg * (1 - a)
        self._img = ImageTk.PhotoImage(Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)))
        self.canvas.delete("orb")
        self.canvas.create_image(px(20) + self.ORB // 2, px(270), image=self._img, tags="orb")
        self.win.after(40, self._animate)

    # ------------------------------------------------------------ pasos
    def render(self) -> None:
        for c in self.extra.winfo_children():
            c.destroy()
        all_slides = slides(self.cfg)
        total = len(all_slides) + 1
        if self.i < 0:
            self.step.config(text="Kalmiya")
            self.title.config(text="Elige tu idioma · Choose your language")
            self.body.config(text="Puedes cambiarlo luego en Configuración. · You can change it later in Settings.")
            self._language_picker()
        else:
            titulo, texto, ejemplos = all_slides[self.i]
            self.step.config(text=f"{self.i + 2} {'of' if self.en else 'de'} {total}")
            self.title.config(text=titulo)
            self.body.config(text=texto)
            for ej in ejemplos:
                tk.Label(self.extra, text=ej, bg=CARD, fg=FG, font=(FONT, 11), padx=12, pady=6, anchor="w").pack(
                    anchor="w", pady=3)
            if self.i == len(all_slides) - 1:
                self._color_picker()
        for c in self.dots.winfo_children():
            c.destroy()
        for j in range(total):
            tk.Label(self.dots, text="●", bg=BG, font=(FONT, 9),
                     fg=self.cfg.color_orbe if j == self.i + 1 else "#3a3c45").pack(side="left", padx=2)
        last = self.i == len(all_slides) - 1
        self.next_btn.config(text=("Start" if self.en else "Empezar") if last else ("Next" if self.en else "Siguiente"),
                             bg=self.cfg.color_orbe)
        self.back_btn.config(text="Back" if self.en else "Atrás", fg=FG if self.i >= 0 else BG,
                             bg=CARD if self.i >= 0 else BG)

    def _language_picker(self) -> None:
        for lang, name in (("es", "Español"), ("en", "English")):
            sel = self.cfg.idioma.startswith(lang)
            box = tk.Frame(self.extra, bg=CARD_HI if sel else CARD, padx=14, pady=10, cursor="hand2",
                           highlightthickness=2, highlightbackground=self.cfg.color_orbe if sel else CARD)
            box.pack(fill="x", pady=4)
            head = tk.Label(box, text=name, bg=box["bg"], fg=FG, font=(FONT, 14, "bold"), anchor="w")
            head.pack(fill="x")
            for wdg in (box, head):
                wdg.bind("<Button-1>", lambda e, l=lang: self._set_language(l))
            if sel:  # voces disponibles en ese idioma, con botón para escucharlas
                for voice, label in VOICES[lang].items():
                    row = tk.Frame(box, bg=box["bg"])
                    row.pack(fill="x", pady=(6, 0))
                    chosen = voice == self.cfg.piper_voz
                    lbl = tk.Label(row, text=("● " if chosen else "○ ") + label, bg=box["bg"],
                                   fg=FG if chosen else MUTED, font=(FONT, 10), cursor="hand2")
                    lbl.pack(side="left")
                    lbl.bind("<Button-1>", lambda e, v=voice: self._set_voice(v))
                    play = tk.Label(row, text="▶  " + ("Listen" if self.en else "Escuchar"), bg=box["bg"],
                                    fg=self.cfg.color_orbe, font=(FONT, 10, "bold"), cursor="hand2")
                    play.pack(side="right")
                    play.bind("<Button-1>", lambda e, v=voice, l=lang: self.preview_voice(v, PREVIEW[l]))

    def _set_language(self, lang: str) -> None:
        if not self.cfg.idioma.startswith(lang):
            self.cfg.idioma = lang
            self.cfg.piper_voz = DEFAULT_VOICE[lang]
        set_lang(lang)
        self.on_language()
        self.render()

    def _set_voice(self, voice: str) -> None:
        self.cfg.piper_voz = voice
        self.on_language()
        self.render()

    def _color_picker(self) -> None:
        row = tk.Frame(self.extra, bg=BG)
        row.pack(anchor="w", pady=(6, 0))
        for nombre, color in COLORES.items():
            sel = color.lower() == self.cfg.color_orbe.lower()
            cell = tk.Frame(row, bg=BG)
            cell.pack(side="left", padx=6)
            sw = tk.Label(cell, bg=color, width=4, height=2, cursor="hand2",
                          highlightthickness=3 if sel else 0, highlightbackground=FG)
            sw.pack()
            tk.Label(cell, text=nombre, bg=BG, fg=FG if sel else MUTED, font=(FONT, 9)).pack(pady=(4, 0))
            sw.bind("<Button-1>", lambda e, c=color: self._set_color(c))

    def _set_color(self, color: str) -> None:
        self.cfg.color_orbe = color
        self.renderer.set_color(color)
        self.on_color(color)
        self.render()

    def next(self) -> None:
        if self.i == len(slides(self.cfg)) - 1:
            self.close()
        else:
            if self.i < 0:  # al salir del paso de idioma, guardarlo ya
                self._set_language(self.cfg.idioma[:2])
            self.i += 1
            self.render()

    def back(self) -> None:
        if self.i >= 0:
            self.i -= 1
            self.render()

    def close(self) -> None:
        self.cfg.primera_vez = False
        self.cfg.save()
        self.win.destroy()
        self.on_close()


def set_icon(win: tk.Toplevel, color: str) -> None:
    """Icono de la ventana = el orbe (en vez de la pluma de Tk)."""
    try:
        from .ui import make_icon
        win._kal_icon = ImageTk.PhotoImage(make_icon(color, 64))
        win.iconphoto(False, win._kal_icon)
    except Exception:
        pass


def _dark_titlebar(win: tk.Toplevel) -> None:
    """Barra de título oscura y esquinas redondeadas de Windows 11."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4)  # modo oscuro
        round_ = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(round_), 4)  # esquinas redondas
    except Exception:
        pass
