"""Texto a voz local: Piper (neuronal) con respaldo en la voz de Windows (SAPI)."""
import logging
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

    def _piper_audio(self, text: str) -> tuple[np.ndarray, int] | None:
        voice = self._load_piper()
        if voice is None:
            return None
        try:
            from piper import SynthesisConfig
            syn = SynthesisConfig(length_scale=1.0 / max(self.speed, 0.1))
        except ImportError:
            syn = None
        parts, rate = [], voice.config.sample_rate
        for chunk in voice.synthesize(text, syn_config=syn) if syn else voice.synthesize(text):
            parts.append(chunk.audio_float_array)
            rate = chunk.sample_rate
        if not parts:
            return None
        return np.concatenate(parts).astype(np.float32), rate

    def _sapi(self, text: str) -> None:
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
                result = self._piper_audio(text)
                if self._cancelled:  # Esc mientras se generaba el audio
                    return
                if result is not None:
                    audio.play(*result)
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
