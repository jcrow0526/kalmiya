"""Grabación de micrófono y reproducción de audio."""
import json
import logging
import os
import subprocess
import sys
import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000

# PortAudio no es seguro entre hilos: abrir/cerrar streams a la vez (el TTS terminando una frase
# mientras el atajo corta la voz y suena el bip) cerraba dos veces el mismo stream y el proceso
# moría con "double free or corruption" dentro del plugin ALSA de PipeWire. Toda apertura y
# cierre de streams pasa por este lock; nunca se toma dentro de un callback de audio.
_pa_lock = threading.Lock()
_playing: sd.OutputStream | None = None


class _FileStream:
    """Hace de micrófono con un audio ya grabado, en tiempo real (pruebas sin micrófono y
    grabaciones de demostración). Al acabarse el audio sigue mandando silencio."""

    BLOCK = 320  # 20 ms

    def __init__(self, samples: np.ndarray, callback):
        self.samples = samples.astype(np.float32)
        self.callback = callback
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        import time
        t0, i = time.monotonic(), 0
        while not self._stop.is_set():
            block = self.samples[i:i + self.BLOCK]
            if len(block) < self.BLOCK:
                block = np.concatenate([block, np.zeros(self.BLOCK - len(block), np.float32)])
            self.callback(block[:, None], self.BLOCK, None, None)
            i += self.BLOCK
            time.sleep(max(0.0, t0 + i / SAMPLE_RATE - time.monotonic()))

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        pass


def load_wav(path: str) -> np.ndarray:
    """WAV mono o estéreo a float32 de 16 kHz."""
    import wave
    with wave.open(path, "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype={2: np.int16, 4: np.int32}[width])
    x = data.reshape(-1, ch).mean(axis=1) / float(2 ** (8 * width - 1))
    if rate != SAMPLE_RATE:
        t = np.arange(0, len(x) / rate, 1 / SAMPLE_RATE)
        x = np.interp(t, np.arange(len(x)) / rate, x)
    return x.astype(np.float32)


class Recorder:
    def __init__(self, device: int | None = None):
        self.device = device
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self.level = 0.0  # nivel RMS para el indicador visual
        self.feeds: list[np.ndarray] = []  # audios que sustituyen al micrófono, uno por grabación

    def _callback(self, indata, frames, time_info, status):
        with self._lock:
            self._chunks.append(indata.copy())
        self.level = float(np.sqrt(np.mean(indata**2)))

    def start(self) -> None:
        self._chunks = []
        if self.feeds:
            stream = _FileStream(self.feeds.pop(0), self._callback)
            stream.start()
            self._stream = stream
            return
        with _pa_lock:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                device=self.device, callback=self._callback,
            )
            try:
                stream.start()
            except Exception:
                stream.close()
                raise
            self._stream = stream

    def stop(self) -> np.ndarray:
        with _pa_lock:  # también evita que dos hilos cierren el mismo stream
            stream, self._stream = self._stream, None
            if stream is not None:
                stream.stop()
                stream.close()
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

    @staticmethod
    def _pactl_inputs() -> list[dict]:
        """Flujos de audio de las demás apps (PipeWire/PulseAudio) con su volumen actual (0-1)."""
        r = subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"], capture_output=True, text=True, timeout=3)
        me = str(os.getpid())
        out = []
        for si in json.loads(r.stdout or "[]"):
            if si.get("properties", {}).get("application.process.id") == me:
                continue
            vols = [v.get("value_percent", "100%") for v in si.get("volume", {}).values()]
            cur = max((float(v.rstrip("%")) for v in vols), default=100.0) / 100
            out.append({"id": str(si["index"]), "volume": cur})
        return out

    @staticmethod
    def _pactl_set(sink_input: str, level: float) -> None:
        subprocess.run(["pactl", "set-sink-input-volume", sink_input, f"{round(level * 100)}%"],
                       capture_output=True, timeout=3)

    def duck(self) -> None:
        with self._lock:
            if self._saved:
                return
            if sys.platform != "win32":
                try:
                    for si in self._pactl_inputs():
                        self._saved[si["id"]] = si["volume"]
                        self._pactl_set(si["id"], si["volume"] * self.level)
                except Exception as e:
                    logging.getLogger(__name__).warning("No pude bajar el volumen: %s", e)
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
            if sys.platform != "win32":
                for sid, cur in self._saved.items():
                    try:
                        self._pactl_set(sid, cur)
                    except Exception as e:
                        logging.getLogger(__name__).warning("No pude restaurar el volumen: %s", e)
                self._saved.clear()
                return
            try:
                for s in self._sessions():
                    cur = self._saved.get(s.InstanceIdentifier)
                    if cur is not None:
                        s.SimpleAudioVolume.SetMasterVolume(cur, None)
            except Exception as e:
                logging.getLogger(__name__).warning("No pude restaurar el volumen: %s", e)
            self._saved.clear()


def _close(stream: sd.OutputStream) -> None:
    """Cierra un stream de reproducción una sola vez, aunque lo intenten varios hilos."""
    global _playing
    with _pa_lock:
        if _playing is stream:
            _playing = None
        if not stream.closed:
            stream.stop()
            stream.close()


def _start(audio: np.ndarray, sample_rate: int) -> tuple[sd.OutputStream, threading.Event]:
    """Empieza a reproducir `audio` cortando lo que sonara antes (como sd.play)."""
    global _playing
    data = np.asarray(audio)
    if data.ndim == 1:
        data = data[:, None]
    done = threading.Event()
    pos = 0

    def callback(outdata, frames, time_info, status):
        nonlocal pos
        chunk = data[pos:pos + frames]
        outdata[:len(chunk)] = chunk
        outdata[len(chunk):] = 0
        pos += frames
        if pos >= len(data):
            raise sd.CallbackStop

    with _pa_lock:
        if _playing is not None and not _playing.closed:
            _playing.stop()
            _playing.close()
        stream = sd.OutputStream(samplerate=sample_rate, channels=data.shape[1], dtype=data.dtype,
                                 callback=callback, finished_callback=done.set)
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        _playing = stream
    return stream, done


def play(audio: np.ndarray, sample_rate: int) -> None:
    stream, done = _start(audio, sample_rate)
    # Si otro hilo lo corta (stop_playback, un bip) el stream queda cerrado: dejar de esperar.
    while not done.wait(0.1) and not stream.closed:
        pass
    _close(stream)


def stop_playback() -> None:
    with _pa_lock:
        stream = _playing
    if stream is not None:
        _close(stream)


def beep(freq: float = 880, ms: int = 70, volume: float = 0.15) -> None:
    """Pequeño 'bip' para indicar inicio/fin de grabación (no bloquea)."""
    t = np.linspace(0, ms / 1000, int(SAMPLE_RATE * ms / 1000), False)
    tone = (np.sin(2 * np.pi * freq * t) * volume).astype(np.float32)
    fade = np.minimum(1, np.minimum(t, t[::-1]) * 200).astype(np.float32)
    try:
        _start(tone * fade, SAMPLE_RATE)
    except Exception:
        pass
