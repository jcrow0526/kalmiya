"""Texto a voz local: Piper (neuronal) con respaldo en la voz de Windows (SAPI)."""
import logging
import queue
import re
import threading

import numpy as np

from . import audio
from .config import MODELS_DIR

log = logging.getLogger(__name__)


_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fechas(text: str) -> str:
    """'2026-10-30' o '30/10/2026' -> '30 de octubre' para que suene natural."""
    text = re.sub(r"\b\d{4}-(\d{2})-(\d{2})\b", lambda m: f"{int(m[2])} de {_MESES[int(m[1]) - 1]}", text)
    return re.sub(r"\b(\d{1,2})/(\d{1,2})/\d{4}\b", lambda m: f"{int(m[1])} de {_MESES[int(m[2]) - 1]}", text)


def _clean(text: str) -> str:
    text = _fechas(text)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"[*_`#>]+", "", text)  # quitar markdown
    text = re.sub(r"https?://\S+", "el enlace", text)
    return text.strip()


class Speaker:
    def __init__(self, voice_file: str, speed: float = 1.0):
        self.voice_path = MODELS_DIR / "piper" / voice_file
        self.speed = speed
        self._voice = None
        self._lock = threading.Lock()
        self._cancelled = False

    def _load_piper(self):
        if self._voice is None and self.voice_path.exists():
            from piper import PiperVoice
            self._voice = PiperVoice.load(str(self.voice_path))
        return self._voice

    def warmup(self) -> None:
        """Carga la voz y genera una palabra: la primera respuesta ya no espera ~3.5 s."""
        if self._load_piper() is not None:
            self._piper_audio("Hola.")

    @staticmethod
    def _pieces(text: str) -> list[str]:
        """Oraciones; la primera se parte en su primera coma para que el primer audio llegue antes
        ("Listo, te recordaré…" suena "Listo," mientras se genera el resto)."""
        sents = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        if sents:
            m = re.match(r"^(.{4,}?[,;:])\s+(.{15,})$", sents[0])
            if m:
                sents[:1] = [m.group(1), m.group(2)]
        return sents

    def chunks(self, text: str):
        """Audio por fragmentos (array, frecuencia), en orden, según se va generando."""
        voice = self._load_piper()
        if voice is None:
            return
        try:
            from piper import SynthesisConfig
            syn = SynthesisConfig(length_scale=1.0 / max(self.speed, 0.1))
        except ImportError:
            syn = None
        for piece in self._pieces(text):
            for chunk in voice.synthesize(piece, syn_config=syn) if syn else voice.synthesize(piece):
                yield chunk.audio_float_array.astype(np.float32), chunk.sample_rate

    def _piper_audio(self, text: str) -> tuple[np.ndarray, int] | None:
        parts = list(self.chunks(text))
        if not parts:
            return None
        return np.concatenate([p for p, _ in parts]), parts[0][1]

    def _stream(self, text: str) -> bool:
        """Reproduce cada fragmento en cuanto está listo mientras se generan los siguientes.
        Devuelve False si Piper no pudo generar nada (para usar la voz de Windows)."""
        q: queue.Queue = queue.Queue()
        failed = []

        def produce():
            try:
                for item in self.chunks(text):
                    if self._cancelled:
                        break
                    q.put(item)
            except Exception as e:
                failed.append(e)
            finally:
                q.put(None)
        threading.Thread(target=produce, daemon=True).start()
        played = False
        while (item := q.get()) is not None:
            if self._cancelled:
                continue  # vaciar la cola sin reproducir
            audio.play(*item)
            played = True
        if failed and not played:
            log.error("Piper falló: %s", failed[0])
            return False
        return played or self._cancelled

    def _sapi(self, text: str) -> None:
        import sys
        if sys.platform != "win32":
            import shutil
            import subprocess
            # Respaldo en Linux: speech-dispatcher o espeak-ng si están instalados.
            for cmd in (["spd-say", "-w", "-l", "es", text], ["espeak-ng", "-v", "es-419", text]):
                if shutil.which(cmd[0]):
                    subprocess.run(cmd, capture_output=True, timeout=60)
                    return
            raise RuntimeError("No hay voz de respaldo (instala espeak-ng)")
        import pyttsx3
        engine = pyttsx3.init()
        for v in engine.getProperty("voices"):
            if "spanish" in v.name.lower() or "español" in v.name.lower() or "es-" in v.id.lower():
                engine.setProperty("voice", v.id)
                break
        engine.setProperty("rate", int(180 * self.speed))
        engine.say(text)
        engine.runAndWait()

    def say(self, text: str) -> None:
        text = _clean(text)
        if not text:
            return
        with self._lock:
            self._cancelled = False
            try:
                if self._stream(text):  # Esc corta tanto la generación como la reproducción
                    return
            except Exception:
                log.exception("Piper falló; usando la voz de Windows")
            try:
                self._sapi(text)
            except Exception:
                log.exception("No se pudo hablar")

    def say_async(self, text: str) -> None:
        threading.Thread(target=self.say, args=(text,), daemon=True).start()

    @property
    def speaking(self) -> bool:
        return self._lock.locked()

    def stop(self) -> None:
        self._cancelled = True
        audio.stop_playback()
