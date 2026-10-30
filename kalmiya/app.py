"""Orquestador: atajo -> grabar -> Whisper -> LFM3 + herramientas -> voz."""
import logging
import socket
import sys
import threading
import time
from pathlib import Path

import numpy as np

from . import LINUX, audio, context, dpi, tools
from .calendar_store import Calendar
from .calendar_ui import CalendarWindow, RemindersWindow
from .config import LOG_DIR, Config, ensure_dirs
from .llm import Agent, LlamaServer
from .stt import make_transcriber
from .tts import Speaker
from .ui import UI
from .i18n import set_lang, t
from .vision import ImagePicker, VisionServer

log = logging.getLogger("kalmiya")



class KalmiyaApp:
    def __init__(self):
        self.cfg = Config.load()
        set_lang(self.cfg.idioma)
        self.recorder = audio.Recorder(self.cfg.microfono)
        self.ducker = audio.Ducker()
        self.stt = make_transcriber(self.cfg)
        self.speaker = Speaker(self.cfg.piper_voz, self.cfg.velocidad_voz)
        self.server = LlamaServer(self.cfg)
        self.agent: Agent | None = None
        self.ui = UI(self.cfg, lambda: self.recorder.level)
        self.ready = False
        self._mode: str | None = None  # "agente" | "dictado" mientras se graba
        self._busy = threading.Lock()
        self._hotkeys: list = []
        self._snapshot: context.Snapshot | None = None
        self._snap_thread: threading.Thread | None = None
        self._unsure_text: str | None = None  # transcripción dudosa esperando "sí"
        self._cancel = False  # Esc: dejar de hablar / de escuchar la respuesta

        self.vision = VisionServer(self.cfg)
        self.calendar = Calendar()
        self.calendar_win = CalendarWindow(self.ui.root, self.calendar, self.cfg.color_orbe, self.ui.post)
        self.reminders_win = RemindersWindow(self.ui.root, self.calendar, self.cfg.color_orbe, self.ui.post)
        # Cualquier cambio (por voz, Google o a mano) refresca las dos ventanas.
        self.calendar.on_change = lambda: self.ui.post(lambda: (self.calendar_win.refresh(), self.reminders_win.refresh()))
        self._settings_win = None

        tools.ctx.config = self.cfg
        tools.ctx.notify = lambda m: self.ui.notify(m)
        tools.ctx.speak = lambda m: self.speaker.say_async(m) if self.cfg.responder_con_voz else None
        tools.ctx.calendar = self.calendar
        tools.ctx.open_calendar = lambda: self.ui.post(self.calendar_win.show)
        tools.ctx.open_reminders = lambda: self.ui.post(self.reminders_win.show)
        self.ui.on_quit = self.shutdown
        self.ui.on_reset = self._new_conversation
        self.ui.on_calendar = self.calendar_win.show
        self.ui.on_reminders = self.reminders_win.show
        self.ui.on_settings = self._open_settings
        self.ui.on_benchmark = self._run_benchmark
        self.ui.on_welcome = self._welcome

    # ------------------------------------------------------------ arranque
    def run(self, lock: socket.socket | None = None) -> None:
        self.ui.start_tray()
        if LINUX:
            from .linux.hotkeys import ControlServer
            ControlServer(lock, self._on_command)
        self._register_hotkeys()
        threading.Thread(target=self._warmup, daemon=True).start()
        self.calendar.start_reminders(self._remind)
        if self.calendar.google_connected:
            self.calendar.start_google_sync()
        if self.cfg.primera_vez:
            self.ui.post(self._welcome)
        if not self.cfg.ciudad:
            threading.Thread(target=self._detect_city, daemon=True).start()
        self.ui.root.mainloop()

    def _run_benchmark(self) -> None:
        """Abre el benchmark en su propia consola (usa el LFM3 que Kalmiya ya tiene cargado)."""
        import shutil
        import subprocess
        cwd = str(Path(__file__).parent.parent)
        if LINUX:
            # xdg-terminal-exec abre la terminal predeterminada (Omarchy y otras); si no, la primera que haya.
            term = next((name for name in ("xdg-terminal-exec", "foot", "kitty", "alacritty", "ghostty", "konsole",
                                     "gnome-terminal") if shutil.which(name)), None)
            if term is None:
                self.ui.status(t("error"), "No encontré una terminal para el benchmark", hide_after=5000)
                return
            sep = ["--"] if term == "gnome-terminal" else ["-e"] if term in ("konsole", "alacritty", "ghostty") else []
            subprocess.Popen([term, *sep, sys.executable, "-m", "kalmiya.bench", "--ask"], cwd=cwd)
            return
        python = Path(sys.executable).with_name("python.exe")  # pythonw no tiene consola
        subprocess.Popen([str(python), "-m", "kalmiya.bench", "--ask"], cwd=cwd,
                         creationflags=subprocess.CREATE_NEW_CONSOLE)

    def _detect_city(self) -> None:
        from .geo import detect_city
        city = detect_city()
        if city and not self.cfg.ciudad:
            log.info("Ciudad detectada: %s", city)
            self.cfg.ciudad = city
            self.cfg.save()

    def _open_settings(self) -> None:
        from .settings_ui import SettingsWindow
        if self._settings_win and self._settings_win.win.winfo_exists():
            self._settings_win.win.deiconify()
            self._settings_win.win.lift()
            return
        self._settings_win = SettingsWindow(
            self.ui.root, self.cfg, on_save=self._apply_settings, preview_color=self.ui.set_color,
            preview_voice=self._preview_voice, open_calendar=self.calendar_win.show,
            open_reminders=self.reminders_win.show,
            connect_google=lambda: (self.calendar_win.show(),
                                    self.ui.root.after(300, self.calendar_win._connect)
                                    if not self.calendar.google_connected else None),
            google_connected=lambda: self.calendar.google_connected)

    def _remind(self, msg: str) -> None:
        self.ui.notify(msg)
        self.ui.status(t("reminder"), msg, hide_after=8000, mode="speaking")
        if self.cfg.responder_con_voz:
            self.speaker.say_async(msg)

    def _welcome(self) -> None:
        from .welcome import Welcome
        Welcome(self.ui.root, self.cfg, on_color=self._set_color, on_close=lambda: None,
                on_language=self._apply_language, preview_voice=self._preview_voice)

    def _preview_voice(self, voice: str, text: str) -> None:
        def run():
            self.speaker.stop()
            Speaker(voice, self.cfg.velocidad_voz).say(text)
        threading.Thread(target=run, daemon=True).start()

    def _apply_language(self) -> None:
        """Idioma y voz elegidos (presentación o configuración): textos, voz, Whisper y respuestas."""
        set_lang(self.cfg.idioma)
        self.speaker = Speaker(self.cfg.piper_voz, self.cfg.velocidad_voz)
        threading.Thread(target=self.speaker.warmup, daemon=True).start()
        if self.agent:
            self.agent.english = self.cfg.idioma.startswith("en")
        if getattr(self.stt, "language", self.cfg.idioma) != self.cfg.idioma:
            self.stt.language = self.cfg.idioma

    def _set_color(self, color: str) -> None:
        self.cfg.color_orbe = color
        self.ui.set_color(color)
        self.ui.set_tray_color(color)
        self.calendar_win.accent = color
        self.reminders_win.accent = color

    def _new_conversation(self) -> None:
        if self.agent:
            self.agent.reset()
        self._unsure_text = None
        self.ui.status(t("new_conv"), t("new_conv_body"), hide_after=1800, mode="speaking")

    def _warmup(self) -> None:
        self.ui.status(t("preparing"), t("loading"))
        # La voz se carga en paralelo con LFM3 y Whisper (antes, la primera respuesta esperaba ~3.5 s).
        threading.Thread(target=self.speaker.warmup, daemon=True).start()
        try:
            self.server.start()
            self.agent = Agent(self.server.base_url, english=self.cfg.idioma.startswith("en"))
            self.stt.load()
            self.ready = True
            log.info("Kalmiya lista")
            self.ui.status(t("ready"), t("ready_hint", k=self.cfg.hotkey_agente), hide_after=3500)
            self.ui.notify(t("ready_notify", k=self.cfg.hotkey_agente))
        except Exception as e:
            log.exception("Fallo al iniciar")
            self.ui.status(t("failed_start"), str(e), hide_after=15000)

    def _on_command(self, cmd: str, args: list[str]) -> None:
        """Órdenes de kalmiya-ctl (atajos de Hyprland, el menú de apps o la terminal). Llegan en orden
        por el mismo hilo: así un "release" nunca adelanta a su "press"."""
        mode = args[0] if args else "agente"
        if cmd == "press":
            self._on_hotkey(mode, "")
        elif cmd == "release":
            if self.cfg.modo_hotkey == "mantener" and self._mode == mode:
                self._finish()
        elif cmd == "toggle":  # empezar/terminar sin mantener pulsado
            if self.recorder.recording:
                self._finish()
            else:
                self._on_hotkey(mode, "")
        elif cmd == "nueva":
            self._new_conversation()
        elif cmd == "imagen":
            self._on_image_hotkey()
        elif cmd == "esc":
            self._on_escape()
        elif cmd in ("settings", "calendar", "reminders", "welcome", "quit"):
            fn = {"settings": self._open_settings, "calendar": self.calendar_win.show,
                  "reminders": self.reminders_win.show, "welcome": self._welcome, "quit": self.ui._quit}[cmd]
            self.ui.post(fn)

    def _register_hotkeys(self) -> None:
        if LINUX:
            from .linux import hotkeys
            hotkeys.install(self.cfg)
            return
        import keyboard
        # keyboard.unhook_all_hotkeys() falla si aún no hay atajos; quitamos los nuestros a mano.
        for h in self._hotkeys:
            keyboard.remove_hotkey(h)
        self._hotkeys = [
            keyboard.add_hotkey(self.cfg.hotkey_agente, lambda: self._on_hotkey("agente", self.cfg.hotkey_agente)),
            keyboard.add_hotkey(self.cfg.hotkey_dictado, lambda: self._on_hotkey("dictado", self.cfg.hotkey_dictado)),
        ]
        for combo, fn in ((self.cfg.hotkey_nueva, self._new_conversation), (self.cfg.hotkey_imagen, self._on_image_hotkey),
                          ("esc", self._on_escape)):
            if combo:
                try:
                    self._hotkeys.append(keyboard.add_hotkey(combo, fn))
                except ValueError:
                    log.warning("Atajo no válido: %s", combo)

    def _apply_settings(self) -> None:
        self._set_color(self.cfg.color_orbe)
        self._apply_language()
        self._register_hotkeys()
        self.stt.stop()
        self.stt = make_transcriber(self.cfg)
        threading.Thread(target=self.stt.load, daemon=True).start()
        self.ui.status(t("settings_saved"), t("settings_saved_body"), hide_after=3000)

    # ------------------------------------------------------------ grabación
    def _on_hotkey(self, mode: str, combo: str) -> None:
        if not self.ready:
            self.ui.status(t("still_preparing"), hide_after=2000)
            return
        if self.recorder.recording:
            if self.cfg.modo_hotkey == "alternar":
                self._finish()
            return
        if self._busy.locked():
            if not self.speaker.speaking:
                return  # está pensando o actuando: no se puede cortar a medias
            # Está hablando: se calla y escucha la nueva orden (la anterior termina sola).
            self._cancel = True
        self.speaker.stop()  # interrumpir si estaba hablando
        self._mode = mode
        if mode == "agente":
            # Qué tiene abierto el usuario, tomado antes de que salga el indicador de Kalmiya;
            # la URL del navegador se lee en paralelo mientras habla.
            self._capture_context(self._foreground())
        try:
            self.recorder.start()
        except Exception as e:
            log.warning("No pude abrir el micrófono: %s", e)
            self._mode = None
            self.ui.status(t("no_mic"), t("no_mic_body"), hide_after=4000)
            return
        if self.cfg.bajar_volumen:
            self.ducker.duck()  # se restaura al terminar de atender la orden
        audio.beep(880)
        self.ui.status(t("listening") if mode == "agente" else t("dictating"), "", listening=True)
        if self.cfg.modo_hotkey == "mantener" and not LINUX:  # en Linux avisa Hyprland al soltar
            threading.Thread(target=self._wait_release, args=(combo,), daemon=True).start()
        if LINUX:
            threading.Thread(target=self._max_length, args=(self.recorder._stream,), daemon=True).start()

    MAX_RECORDING = 60  # s; por si Hyprland no avisó de que se soltó el atajo

    def _max_length(self, stream) -> None:
        time.sleep(self.MAX_RECORDING)
        if self.recorder.recording and self.recorder._stream is stream:
            self._finish()

    @staticmethod
    def _foreground():
        if LINUX:
            from .linux import desktop
            w = desktop.active_window()
            return w["address"] if w else ""
        import win32gui
        return win32gui.GetForegroundWindow()

    def _on_escape(self) -> None:
        """Esc: callarla y no seguir escuchando una respuesta. No hace nada si Kalmiya está quieta
        (la tecla sigue llegando a la ventana activa)."""
        if not (self.speaker.speaking or self._busy.locked()):
            return
        self._cancel = True
        self.speaker.stop()
        self.ui.hide()

    def _wait_release(self, combo: str) -> None:
        import keyboard
        time.sleep(0.15)
        while keyboard.is_pressed(combo):
            time.sleep(0.03)
        self._finish()

    def _finish(self) -> None:
        if not self.recorder.recording:
            return
        clip = self.recorder.stop()
        audio.beep(660)
        if LINUX and self._mode == "agente" and self._snap_thread:
            # La URL se copia de la barra del navegador ya con las teclas del atajo sueltas.
            snap_thread = self._snap_thread

            def read_url():
                snap_thread.join(timeout=2)
                if self._snapshot:
                    context.read_url(self._snapshot)
            self._snap_thread = threading.Thread(target=read_url, daemon=True)
            self._snap_thread.start()
        threading.Thread(target=self._process, args=(self._mode, clip), daemon=True).start()

    def _capture_context(self, foreground: int) -> None:
        def run():
            try:
                self._snapshot = context.capture(foreground, with_url=not LINUX)
            except Exception:
                log.exception("No pude leer las ventanas abiertas")
                self._snapshot = None
        self._snap_thread = threading.Thread(target=run, daemon=True)
        self._snap_thread.start()

    # ------------------------------------------------------------ procesamiento
    # Por debajo de esto Whisper probablemente entendió mal; mejor preguntar "¿Dijiste …?".
    MIN_CONFIDENCE = 0.55

    def _process(self, mode: str, clip) -> None:
        with self._busy:
            self._set_busy(True)
            try:
                # Si Kalmiya termina con una pregunta, escucha la respuesta sin que pulses el atajo.
                answering = False
                self._cancel = False
                while clip is not None:
                    reply = self._handle(mode, clip, answering)
                    clip = None
                    answering = bool(reply) and reply.rstrip().endswith("?") and mode == "agente"
                    if answering and self.cfg.escuchar_respuesta and not self._cancel:
                        clip = self._listen_answer()
            except Exception as e:
                log.exception("Error procesando")
                self.ui.status(t("error"), str(e), hide_after=6000)
            finally:
                if not self.recorder.recording:  # si la interrumpieron con el atajo, sigue bajado
                    self.ducker.restore()
                self._set_busy(False)

    @staticmethod
    def _set_busy(busy: bool) -> None:
        if LINUX:  # Esc solo se intercepta mientras habla o piensa
            from .linux.hotkeys import set_busy
            set_busy(busy)

    def _handle(self, mode: str, clip, answering: bool = False) -> str:
        self.ui.status(t("transcribing"))
        text = self.stt.transcribe(clip, answer=answering)
        confidence = getattr(self.stt, "confidence", 1.0)
        log.info("Usuario (%s, confianza %.2f): %s", mode, confidence, text)
        if not text:
            self.ui.status(t("not_heard"), hide_after=1500)
            return ""
        if mode == "dictado":
            self.ui.hide()
            time.sleep(0.1)
            tools.paste_text(text)
            return ""
        if self._unsure_text is not None:  # respuesta a "¿Dijiste …?"
            from .llm import yes_no
            dudoso, self._unsure_text = self._unsure_text, None
            answer = yes_no(text)
            if answer is True:
                text, confidence = dudoso, 1.0
            elif answer is False and len(text.split()) <= 3:
                return self._say(t("say_again"), ask=True)
        if confidence < self.MIN_CONFIDENCE and self.cfg.confirmar_si_dudo and len(text.split()) > 1:
            self._unsure_text = text
            return self._say(t("did_you_say", x=text.rstrip(".")), ask=True)
        if self._snap_thread:
            self._snap_thread.join(timeout=2)
        self.ui.status(t("thinking"), f"“{text}”")
        tools.ctx.snapshot = self._snapshot
        tools.ctx.later.clear()
        reply = self.agent.run(
            text, contexto=self._snapshot.describe() if self._snapshot else "",
            on_tool=lambda n, a: self.ui.status(t(f"tool_{n}") + "…", f"“{text}”"))
        log.info("Kalmiya: %s", reply)
        return self._say(reply or "Hecho")

    def _say(self, reply: str, ask: bool = False) -> str:
        self.ui.status("Kalmiya", reply, hide_after=None if ask else max(3000, 60 * len(reply)))
        if self.cfg.responder_con_voz:
            self.speaker.say(reply)
        self._run_later()
        return reply

    def _run_later(self) -> None:
        """Lo que las herramientas dejaron para cuando el orbe no estorbe (capturas, apagar…)."""
        later, tools.ctx.later[:] = list(tools.ctx.later), []
        if not later:
            return
        self.ui.hide()
        time.sleep(0.4)  # que el orbe desaparezca antes de capturar
        for fn in later:
            try:
                fn()
            except Exception:
                log.exception("Acción diferida falló")

    def _listen_answer(self, wait: float = 5.0, silence: float = 0.8, max_len: float = 10.0,
                       hint: str | None = None, quiet: bool = False):
        """Graba la respuesta a una pregunta: empieza al hablar y termina tras un silencio.
        `quiet`: si no habla, devolver None sin cancelar nada pendiente."""
        time.sleep(0.15)
        self.recorder.start()
        audio.beep(880)
        self.ui.status(t("listen_answer"), hint or t("answer_hint"), listening=True)
        start = time.time()
        levels: list[float] = []
        spoke, last_voice = False, start
        while True:
            time.sleep(0.05)
            if self._cancel:  # Esc
                self.recorder.stop()
                self.agent.pending = None
                self._unsure_text = None
                return None
            now = time.time()
            levels.append(self.recorder.level)
            if now - start < 0.3:  # el eco del bip
                continue
            # Ruido de fondo = lo más bajo de esos 0.3 s: con la mediana entraba el bip y el umbral
            # quedaba en ~0.02, por encima de un "sí" con un micrófono USB bajito (fondo ~0.0003).
            threshold = max(0.006, 4 * float(np.percentile(levels[:6], 20)))
            if levels[-1] > threshold:
                spoke, last_voice = True, now
            if (not spoke and now - start > wait) or (spoke and now - last_voice > silence) or now - start > max_len:
                break
        clip = self.recorder.stop()
        audio.beep(660)
        if not spoke and quiet:
            return None
        if not spoke:
            log.info("Sin respuesta: nivel máximo %.4f, umbral %.4f", max(levels[6:], default=0.0), threshold)
            self._unsure_text = None
            borrador = getattr(self.agent.pending, "al_cancelar", None) is not None
            self.agent.pending = None
            # Un mensaje sin respuesta se queda escrito en el chat, sin enviar.
            self.ui.status(t("draft_kept") if borrador else t("dropped"),
                           hide_after=3000 if borrador else 1500)
            return None
        return clip

    # ------------------------------------------------------------ imágenes
    def _on_image_hotkey(self) -> None:
        if not self.ready or self._busy.locked() or self.recorder.recording:
            return
        if not self.vision.available:
            self.ui.status(t("vision_missing"), t("vision_missing_body"), hide_after=5000)
            return
        self.speaker.stop()
        threading.Thread(target=self._warm_vision, daemon=True).start()  # carga mientras eliges
        self.ui.post(lambda: ImagePicker(self.ui.root, self.cfg.color_orbe, self._on_image_picked))

    def _warm_vision(self) -> None:
        try:
            self.vision.start()
        except Exception:
            log.exception("No pude iniciar el modelo de visión")

    def _on_image_picked(self, img) -> None:
        if img is not None:
            threading.Thread(target=self._ask_image, args=(img,), daemon=True).start()

    def _ask_image(self, img) -> None:
        with self._busy:
            self._set_busy(True)
            try:
                self._cancel = False
                if self.cfg.bajar_volumen:
                    self.ducker.duck()
                self._say(t("image_what"), ask=True)
                if self._cancel:
                    return
                clip = self._listen_answer(wait=6, hint=t("image_hint"),
                                           quiet=True)
                if self._cancel:
                    return
                question = self.stt.transcribe(clip) if clip is not None else ""
                log.info("Pregunta sobre imagen: %s", question)
                question = question or t("image_default")
                self.ui.status(t("image_looking"), f"“{question}”", mode="thinking")
                reply = self.vision.ask(img, question)
                log.info("Kalmiya (imagen): %s", reply)
                self._say(reply or t("image_none"))
            except Exception as e:
                log.exception("Error con la imagen")
                self.ui.status(t("image_error"), str(e), hide_after=6000)
            finally:
                self.ducker.restore()
                self._set_busy(False)

    def shutdown(self) -> None:
        self.ducker.restore()
        if not LINUX:
            import keyboard
            keyboard.unhook_all()
        self.vision.stop()
        self.server.stop()
        self.stt.stop()


def _single_instance() -> socket.socket | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if LINUX:  # en Linux no permite dos a la vez, solo reabrir tras un cierre (TIME_WAIT)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", 47653))
        return s
    except OSError:
        return None


def main() -> None:
    ensure_dirs()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOG_DIR / "kalmiya.log", encoding="utf-8"), logging.StreamHandler(sys.stderr)]
        if sys.stderr else [logging.FileHandler(LOG_DIR / "kalmiya.log", encoding="utf-8")],
    )
    for noisy in ("httpx", "httpx2", "httpcore", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if LINUX and len(sys.argv) > 1:  # `python -m kalmiya settings`: órdenes a la que ya está abierta
        from .linux.hotkeys import send
        sys.exit(0 if send(" ".join(sys.argv[1:])) else 1)
    lock = _single_instance()
    if lock is None:
        log.info("Kalmiya ya está en ejecución")
        if LINUX:  # abrirla otra vez desde el menú de apps: mostrar la configuración
            from .linux.hotkeys import send
            send("settings")
        return
    dpi.enable()  # antes de crear Tk
    KalmiyaApp().run(lock)
