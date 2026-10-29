"""Ventana del calendario de Kalmiya: vista de mes, eventos del día y conexión con Google."""
import calendar as pycal
import datetime as dt
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

from .calendar_store import DIAS, MESES, Calendar
from .config import GOOGLE_CLIENT_FILE
from .dpi import px

BG, PANEL, CELL, CELL_HOVER, FG, MUTED, LINE = "#101114", "#17181d", "#1c1e24", "#24262e", "#f2f2f5", "#8e909c", "#2a2c34"
DIM_FG = "#6b6d78"
FONT = "Segoe UI"

GOOGLE_HELP = (
    "Para conectar Google Calendar necesitas un archivo de credenciales (una sola vez):\n\n"
    "1. Entra a console.cloud.google.com y crea un proyecto.\n"
    "2. En «APIs y servicios» activa «Google Calendar API».\n"
    "3. En «Pantalla de consentimiento de OAuth» elige Externo y agrégate como usuario de prueba.\n"
    "4. En «Credenciales» → Crear credenciales → ID de cliente de OAuth → «App de escritorio».\n"
    "5. Descarga el JSON y elígelo en la siguiente ventana.\n\n"
    "Después se abrirá tu navegador para que autorices a Kalmiya."
)


class CalendarWindow:
    def __init__(self, root: tk.Misc, cal: Calendar, accent: str, post):
        self.root, self.cal, self.accent, self.post = root, cal, accent, post
        self.win: tk.Toplevel | None = None
        self.month = dt.date.today().replace(day=1)
        self.selected = dt.date.today()
        cal.on_change = lambda: self.post(self.refresh)

    # ------------------------------------------------------------ ventana
    def show(self) -> None:
        if self.win and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            return
        w = self.win = tk.Toplevel(self.root)
        w.title("Kalmiya — Calendario")
        w.configure(bg=BG)
        w.geometry(f"{px(980)}x{px(620)}")
        w.minsize(px(860), px(560))
        from .welcome import _dark_titlebar
        _dark_titlebar(w)
        from .welcome import set_icon
        set_icon(w, self.accent)

        # El panel derecho se empaqueta primero para que la cuadrícula no lo aplaste.
        right = self.side = tk.Frame(w, bg=PANEL, width=px(330), padx=px(20), pady=px(20))
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        left = tk.Frame(w, bg=BG, padx=24, pady=20)
        left.pack(side="left", fill="both", expand=True)

        head = tk.Frame(left, bg=BG)
        head.pack(fill="x", pady=(0, 14))
        self.month_lbl = tk.Label(head, bg=BG, fg=FG, font=(FONT, 20, "bold"))
        self.month_lbl.pack(side="left")
        for txt, cmd in (("›", lambda: self._move(1)), ("‹", lambda: self._move(-1))):
            self._button(head, txt, cmd, size=14, width=3).pack(side="right", padx=2)
        self._button(head, "Hoy", self._today).pack(side="right", padx=8)

        names = tk.Frame(left, bg=BG)
        names.pack(fill="x")
        for i, d in enumerate(DIAS):
            names.columnconfigure(i, weight=1, uniform="d")
            tk.Label(names, text=d[:3].upper(), bg=BG, fg=MUTED, font=(FONT, 9, "bold")).grid(row=0, column=i, pady=(0, 6))
        self.grid = tk.Frame(left, bg=BG)
        self.grid.pack(fill="both", expand=True)
        for i in range(7):
            self.grid.columnconfigure(i, weight=1, uniform="d")
        for r in range(6):
            self.grid.rowconfigure(r, weight=1, uniform="w")

        self.day_lbl = tk.Label(right, bg=PANEL, fg=FG, font=(FONT, 15, "bold"), anchor="w", justify="left")
        self.day_lbl.pack(fill="x")
        self.list = tk.Frame(right, bg=PANEL)
        self.list.pack(fill="both", expand=True, pady=(12, 8))

        form = tk.Frame(right, bg=PANEL)
        form.pack(fill="x")
        tk.Label(form, text="Nuevo evento", bg=PANEL, fg=MUTED, font=(FONT, 9, "bold")).pack(anchor="w")
        self.title_var, self.hour_var, self.dur_var = tk.StringVar(), tk.StringVar(value="09:00"), tk.StringVar(value="60")
        self._entry(form, self.title_var, "Título").pack(fill="x", pady=(6, 6))
        row = tk.Frame(form, bg=PANEL)
        row.pack(fill="x")
        self._entry(row, self.hour_var, "Hora", width=8).pack(side="left")
        tk.Label(row, text="min", bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(side="right")
        self._entry(row, self.dur_var, "Minutos", width=5).pack(side="right", padx=(8, 4))
        self._button(form, "Agregar", self._add, primary=True).pack(fill="x", pady=(8, 0))

        g = tk.Frame(right, bg=PANEL)
        g.pack(fill="x", side="bottom")
        tk.Frame(g, bg=LINE, height=1).pack(fill="x", pady=(0, 12))
        self.google_lbl = tk.Label(g, bg=PANEL, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=px(290))
        self.google_lbl.pack(fill="x")
        self.google_btns = tk.Frame(g, bg=PANEL)
        self.google_btns.pack(fill="x", pady=(8, 0))
        self.refresh()

    def _button(self, parent, text, cmd, primary=False, size=10, width=None):
        bg = self.accent if primary else CELL
        fg = "#0b0c0e" if primary else FG
        b = tk.Label(parent, text=text, bg=bg, fg=fg, font=(FONT, size, "bold" if primary else "normal"),
                     padx=12, pady=6, cursor="hand2", width=width)
        b.bind("<Button-1>", lambda e: cmd())
        if not primary:
            b.bind("<Enter>", lambda e: b.config(bg=CELL_HOVER))
            b.bind("<Leave>", lambda e: b.config(bg=CELL))
        return b

    def _entry(self, parent, var, placeholder, width=None):
        e = tk.Entry(parent, textvariable=var, bg=CELL, fg=FG, insertbackground=FG, relief="flat",
                     font=(FONT, 11), highlightthickness=1, highlightbackground=LINE, highlightcolor=self.accent)
        if width:
            e.config(width=width)
        if not var.get():
            var.set(placeholder)
            e.config(fg=MUTED)

            def clear(_):
                if e.cget("fg") == MUTED:
                    var.set("")
                    e.config(fg=FG)
            e.bind("<FocusIn>", clear)
        e.bind("<Return>", lambda _: self._add())
        return e

    # ------------------------------------------------------------ acciones
    def _move(self, n: int) -> None:
        y, m = divmod(self.month.month - 1 + n, 12)
        self.month = dt.date(self.month.year + y, m + 1, 1)
        self.refresh()

    def _today(self) -> None:
        self.selected = dt.date.today()
        self.month = self.selected.replace(day=1)
        self.refresh()

    def _select(self, d: dt.date) -> None:
        self.selected = d
        if d.month != self.month.month:
            self.month = d.replace(day=1)
        self.refresh()

    def _add(self) -> None:
        titulo = self.title_var.get().strip()
        if not titulo or titulo == "Título":
            return
        try:
            h, m = (self.hour_var.get().replace(".", ":").split(":") + ["0"])[:2]
            inicio = dt.datetime.combine(self.selected, dt.time(int(h), int(m)))
            minutos = int(self.dur_var.get() or 60)
        except ValueError:
            messagebox.showerror("Kalmiya", "La hora debe ser como 16:30 y la duración en minutos.", parent=self.win)
            return
        self.cal.add(titulo, inicio, minutos)
        self.title_var.set("")

    def _connect(self) -> None:
        client = None
        if not GOOGLE_CLIENT_FILE.exists():
            if not messagebox.askokcancel("Conectar Google Calendar", GOOGLE_HELP, parent=self.win):
                return
            client = filedialog.askopenfilename(parent=self.win, title="Credenciales de Google (JSON)",
                                                filetypes=[("JSON", "*.json")])
            if not client:
                return
        self.google_lbl.config(text="Esperando la autorización en tu navegador…")

        def run():
            try:
                self.cal.connect_google(client)
                self.post(self.refresh)
            except Exception as e:
                self.post(lambda: messagebox.showerror("Kalmiya", f"No pude conectar con Google:\n{e}", parent=self.win))
                self.post(self.refresh)
        threading.Thread(target=run, daemon=True).start()

    def _sync(self) -> None:
        self.google_lbl.config(text="Sincronizando…")
        threading.Thread(target=lambda: (self.cal.sync_google(), self.post(self.refresh)), daemon=True).start()

    # ------------------------------------------------------------ dibujo
    def refresh(self) -> None:
        if not (self.win and self.win.winfo_exists()):
            return
        self.month_lbl.config(text=f"{MESES[self.month.month - 1].capitalize()} {self.month.year}")
        for c in self.grid.winfo_children():
            c.destroy()
        hoy = dt.date.today()
        weeks = pycal.Calendar(firstweekday=0).monthdatescalendar(self.month.year, self.month.month)
        for r, week in enumerate(weeks):
            for c, d in enumerate(week):
                self._cell(r, c, d, hoy)
        self._side()

    def _cell(self, r: int, c: int, d: dt.date, hoy: dt.date) -> None:
        sel = d == self.selected
        bg = CELL_HOVER if sel else CELL
        f = tk.Frame(self.grid, bg=bg, padx=6, pady=4, highlightthickness=2 if sel else 0,
                     highlightbackground=self.accent, cursor="hand2")
        f.grid(row=r, column=c, sticky="nsew", padx=2, pady=2)
        otro_mes = d.month != self.month.month
        num_fg = "#0b0c0e" if d == hoy else (MUTED if otro_mes else FG)
        num = tk.Label(f, text=str(d.day), bg=self.accent if d == hoy else bg, fg=num_fg,
                       font=(FONT, 10, "bold"), padx=5)
        num.pack(anchor="nw")
        widgets = [f, num]
        evs = self.cal.on_day(d)
        for ev in evs[:2]:
            t = "" if ev.todo_el_dia else ev.start.strftime("%H:%M ")
            if ev.es_recordatorio:
                t = "⏰ " + t
            lbl = tk.Label(f, text=f"{t}{ev.titulo}"[:22], bg=bg, fg=self.accent if ev.origen == "eco" else "#8ab4f8",
                           font=(FONT, 8), anchor="w")
            lbl.pack(fill="x")
            widgets.append(lbl)
        if len(evs) > 2:
            more = tk.Label(f, text=f"+{len(evs) - 2} más", bg=bg, fg=MUTED, font=(FONT, 8), anchor="w")
            more.pack(fill="x")
            widgets.append(more)
        for wd in widgets:
            wd.bind("<Button-1>", lambda e, d=d: self._select(d))

    def _side(self) -> None:
        d = self.selected
        self.day_lbl.config(text=f"{DIAS[d.weekday()].capitalize()}\n{d.day} de {MESES[d.month - 1]}")
        for c in self.list.winfo_children():
            c.destroy()
        evs = self.cal.on_day(d)
        if not evs:
            tk.Label(self.list, text="Sin eventos.\nDile a Kalmiya «agéndame…» o agrégalo abajo.", bg=PANEL, fg=MUTED,
                     font=(FONT, 10), justify="left").pack(anchor="w")
        for ev in evs:
            row = tk.Frame(self.list, bg=CELL, padx=10, pady=8)
            row.pack(fill="x", pady=3)
            tk.Frame(row, bg=self.accent if ev.origen == "eco" else "#8ab4f8", width=3).pack(side="left", fill="y", padx=(0, 10))
            info = tk.Frame(row, bg=CELL)
            info.pack(side="left", fill="x", expand=True)
            hora = ("Todo el día" if ev.todo_el_dia else f"⏰ Recordatorio · {ev.start:%H:%M}" if ev.es_recordatorio
                    else f"{ev.start:%H:%M} – {ev.end:%H:%M}")
            tk.Label(info, text=ev.titulo, bg=CELL, fg=FG, font=(FONT, 10, "bold"), anchor="w").pack(fill="x")
            extra = hora + (f" · {ev.lugar}" if ev.lugar else "") + (" · Google" if ev.origen == "google" else "")
            tk.Label(info, text=extra, bg=CELL, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")
            x = tk.Label(row, text="✕", bg=CELL, fg=MUTED, font=(FONT, 10), cursor="hand2")
            x.pack(side="right")
            x.bind("<Button-1>", lambda e, ev=ev: self._delete(ev))

        for c in self.google_btns.winfo_children():
            c.destroy()
        if self.cal.google_connected:
            self.google_lbl.config(text="● Google Calendar conectado. Lo que agendes con Kalmiya también se guarda ahí.")
            self._button(self.google_btns, "Sincronizar", self._sync).pack(side="left")
            self._button(self.google_btns, "Desconectar", self._disconnect).pack(side="right")
        else:
            self.google_lbl.config(text="Kalmiya guarda tus eventos en esta PC. Conecta Google Calendar para verlos "
                                        "también en el teléfono.")
            self._button(self.google_btns, "Conectar Google Calendar", self._connect, primary=True).pack(fill="x")

    def _delete(self, ev) -> None:
        if messagebox.askyesno("Kalmiya", f"¿Borrar «{ev.titulo}»?", parent=self.win):
            self.cal.delete(ev.id)

    def _disconnect(self) -> None:
        if messagebox.askyesno("Kalmiya", "¿Desconectar Google Calendar? Tus eventos de Kalmiya se quedan en esta PC.",
                               parent=self.win):
            self.cal.disconnect_google()


class RemindersWindow:
    """Lista de recordatorios: pendientes y ya avisados de hoy, con alta y borrado."""

    def __init__(self, root: tk.Misc, cal: Calendar, accent: str, post):
        self.root, self.cal, self.accent, self.post = root, cal, accent, post
        self.win: tk.Toplevel | None = None

    def show(self) -> None:
        if self.win and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            self.refresh()
            return
        w = self.win = tk.Toplevel(self.root)
        w.title("Kalmiya — Recordatorios")
        w.configure(bg=BG)
        W, H = px(520), px(620)
        w.geometry(f"{W}x{H}+{(w.winfo_screenwidth() - W) // 2}+{(w.winfo_screenheight() - H) // 2}")
        from .welcome import _dark_titlebar, set_icon
        _dark_titlebar(w)
        set_icon(w, self.accent)

        body = tk.Frame(w, bg=BG, padx=px(24), pady=px(20))
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Recordatorios", bg=BG, fg=FG, font=(FONT, 20, "bold"), anchor="w").pack(fill="x")
        tk.Label(body, text="Dile a Kalmiya «recuérdame … en 20 minutos» o «mañana a las 9». También salen en el "
                            "calendario.", bg=BG, fg=MUTED, font=(FONT, 10), anchor="w", justify="left",
                 wraplength=px(460)).pack(fill="x", pady=(px(2), px(14)))

        form = tk.Frame(body, bg=PANEL, padx=px(14), pady=px(12))
        form.pack(fill="x")
        self.text_var = tk.StringVar()
        self.when_var = tk.StringVar(value="en 30 minutos")
        tk.Label(form, text="Qué", bg=PANEL, fg=MUTED, font=(FONT, 9, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(form, text="Cuándo", bg=PANEL, fg=MUTED, font=(FONT, 9, "bold")).grid(row=0, column=1, sticky="w",
                                                                                    padx=(px(10), 0))
        for col, var, width in ((0, self.text_var, 24), (1, self.when_var, 16)):
            e = tk.Entry(form, textvariable=var, width=width, bg=CELL, fg=FG, insertbackground=FG, relief="flat",
                         font=(FONT, 11), highlightthickness=1, highlightbackground=LINE, highlightcolor=self.accent)
            e.grid(row=1, column=col, sticky="we", padx=(px(10) if col else 0, 0), pady=(px(4), 0), ipady=px(3))
            e.bind("<Return>", lambda _: self._add())
        form.columnconfigure(0, weight=1)
        add = tk.Label(form, text="Agregar", bg=self.accent, fg="#0b0c0e", font=(FONT, 10, "bold"), padx=px(14),
                       pady=px(6), cursor="hand2")
        add.grid(row=1, column=2, padx=(px(10), 0), pady=(px(4), 0))
        add.bind("<Button-1>", lambda e: self._add())
        tk.Label(form, text="Ej.: «en 2 horas», «mañana 9:00», «viernes 18:30», «20:15»", bg=PANEL, fg=DIM_FG,
                 font=(FONT, 8)).grid(row=2, column=0, columnspan=3, sticky="w", pady=(px(6), 0))

        self.list = tk.Frame(body, bg=BG)
        self.list.pack(fill="both", expand=True, pady=(px(14), 0))
        self.refresh()

    def _add(self) -> None:
        import re
        from . import tools
        texto = self.text_var.get().strip()
        cuando = self.when_var.get().strip().lower()
        if not texto:
            return
        m = re.match(r"en\s+(\d+(?:[.,]\d+)?)\s*(min|minutos?|h|horas?)", cuando)
        try:
            if m:
                n = float(m.group(1).replace(",", "."))
                mins = n * 60 if m.group(2).startswith("h") else n
                tools.recordatorio(texto, minutos=mins)
            else:
                hm = re.search(r"(\d{1,2})(?::(\d{2}))?", cuando)
                fecha = re.sub(r"\d{1,2}(:\d{2})?", "", cuando).strip()
                hora = f"{hm.group(1)}:{hm.group(2) or '00'}" if hm else ""
                out = tools.recordatorio(texto, fecha=fecha, hora=hora)
                if out.startswith(("Error", "¿")):
                    messagebox.showinfo("Kalmiya", out.removeprefix("Error: "), parent=self.win)
                    return
        except Exception:
            messagebox.showerror("Kalmiya", "No entendí cuándo. Prueba «en 20 minutos» o «mañana 9:00».",
                                 parent=self.win)
            return
        self.text_var.set("")
        self.refresh()

    def refresh(self) -> None:
        if not (self.win and self.win.winfo_exists()):
            return
        from .calendar_store import spoken_date
        for c in self.list.winfo_children():
            c.destroy()
        now = dt.datetime.now()
        pend = self.cal.reminders()
        hechos = [e for e in self.cal.reminders(include_done=True) if e.start < now and e.start.date() == now.date()]
        if not pend and not hechos:
            tk.Label(self.list, text="No tienes recordatorios.", bg=BG, fg=MUTED, font=(FONT, 10)).pack(anchor="w")
        for titulo, evs, done in (("Pendientes", pend, False), ("Hoy, ya avisados", hechos, True)):
            if not evs:
                continue
            tk.Label(self.list, text=titulo.upper(), bg=BG, fg=DIM_FG, font=(FONT, 8, "bold"), anchor="w").pack(
                fill="x", pady=(px(8), px(4)))
            for ev in evs:
                row = tk.Frame(self.list, bg=CELL, padx=px(12), pady=px(9))
                row.pack(fill="x", pady=px(2))
                tk.Label(row, text="⏰", bg=CELL, fg=MUTED if done else self.accent, font=(FONT, 12)).pack(
                    side="left", padx=(0, px(10)))
                info = tk.Frame(row, bg=CELL)
                info.pack(side="left", fill="x", expand=True)
                tk.Label(info, text=ev.titulo, bg=CELL, fg=MUTED if done else FG, font=(FONT, 10, "bold"),
                         anchor="w").pack(fill="x")
                cuando = f"{spoken_date(ev.start.date()).capitalize()} · {ev.start:%H:%M}"
                tk.Label(info, text=cuando, bg=CELL, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")
                x = tk.Label(row, text="✕", bg=CELL, fg=MUTED, font=(FONT, 10), cursor="hand2")
                x.pack(side="right")
                x.bind("<Button-1>", lambda e, ev=ev: (self.cal.delete(ev.id), self.refresh()))
