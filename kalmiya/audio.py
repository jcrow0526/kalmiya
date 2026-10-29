"""Grabación de micrófono y reproducción de audio."""
import logging
import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000


class Recorder:
    def __init__(self, device: int | None = None):
        self.device = device
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self.level = 0.0  # nivel RMS para el indicador visual

    def _callback(self, indata, frames, time_info, status):
        with self._lock:
            self._chunks.append(indata.copy())
        self.level = float(np.sqrt(np.mean(indata**2)))

    def start(self) -> None:
        self._chunks = []
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            device=self.device, callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self.level = 0.0
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._chunks)[:, 0]

    @property
    def recording(self) -> bool:
        return self._stream is not None


class Ducker:
    """Baja el volumen de las demás apps (YouTube, Spotify…) mientras el usuario habla con Kalmiya.

    El audio del video se colaba en la grabación ("que haces vos" en vez de "qué hace que
    Escocia") y, al escuchar una respuesta, Kalmiya tomaba frases del video como órdenes.
    Se guarda el volumen original de cada sesión por su identificador (no el objeto COM,
    que no se puede usar desde otro hilo) y se restaura al terminar.
    """

    def __init__(self, level: float = 0.15):
        self.level = level
        self._saved: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _sessions():
        import os

        import comtypes
        from pycaw.pycaw import AudioUtilities
        comtypes.CoInitialize()  # el atajo llega en el hilo de `keyboard`
        me = os.getpid()
        for s in AudioUtilities.GetAllSessions():
            if s.ProcessId not in (0, me):
                yield s

    def duck(self) -> None:
        with self._lock:
            if self._saved:
                return
            try:
                for s in self._sessions():
                    vol = s.SimpleAudioVolume
                    cur = vol.GetMasterVolume()
                    self._saved[s.InstanceIdentifier] = cur
                    vol.SetMasterVolume(cur * self.level, None)
            except Exception as e:
                logging.getLogger(__name__).warning("No pude bajar el volumen: %s", e)

    def restore(self) -> None:
        with self._lock:
            if not self._saved:
                return
            try:
                for s in self._sessions():
                    cur = self._saved.get(s.InstanceIdentifier)
                    if cur is not None:
                        s.SimpleAudioVolume.SetMasterVolume(cur, None)
            except Exception as e:
                logging.getLogger(__name__).warning("No pude restaurar el volumen: %s", e)
            self._saved.clear()


def play(audio: np.ndarray, sample_rate: int) -> None:
    sd.play(audio, sample_rate)
    sd.wait()


def stop_playback() -> None:
    sd.stop()


def beep(freq: float = 880, ms: int = 70, volume: float = 0.15) -> None:
    """Pequeño 'bip' para indicar inicio/fin de grabación (no bloquea)."""
    t = np.linspace(0, ms / 1000, int(SAMPLE_RATE * ms / 1000), False)
    tone = (np.sin(2 * np.pi * freq * t) * volume).astype(np.float32)
    fade = np.minimum(1, np.minimum(t, t[::-1]) * 200).astype(np.float32)
    try:
        sd.play(tone * fade, SAMPLE_RATE)
    except Exception:
        pass
