"""Voz a texto local: faster-whisper o NVIDIA Nemotron (NeMo-Speech.cpp)."""
import logging
import re
from pathlib import Path

import numpy as np

from .config import MODELS_DIR

log = logging.getLogger(__name__)


def _add_cuda_dlls() -> None:
    """Hace visibles cuBLAS/cuDNN instalados como wheels de pip (nvidia-*-cu12) para CTranslate2."""
    import os
    import sys

    if sys.platform != "win32":
        return  # en Linux el lanzador (kalmiya.sh) ya pone las libs de nvidia-* en LD_LIBRARY_PATH
    root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    for bin_dir in root.glob("*/bin"):
        os.add_dll_directory(str(bin_dir))
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def _cuda_compute_type() -> str:
    """float16 solo rinde con tensor cores (RTX). En una GTX 1660, large-v3-turbo tardaba 4.5 s por
    frase en float16 y 1.1 s en int8_float16, con el mismo texto y menos VRAM."""
    import subprocess

    from .llm import NO_WINDOW
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,compute_cap", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW).stdout
        name, cap = [x.strip() for x in out.splitlines()[0].split(",")]
        if "GTX" in name.upper() or float(cap) < 7.0:
            return "int8_float16"
    except Exception:
        pass
    return "float16"


class Transcriber:
    def __init__(self, model_name: str = "small", language: str = "es"):
        self.model_name = model_name
        self.language = language
        self._model = None
        self.confidence = 1.0  # de la última transcripción (0-1, media de probabilidad por token)

    def load(self) -> None:
        if self._model is not None:
            return
        _add_cuda_dlls()
        from faster_whisper import WhisperModel

        device, compute = "cpu", "int8"
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                device, compute = "cuda", _cuda_compute_type()
        except Exception:
            pass
        root = str(MODELS_DIR / "whisper")
        log.info("Cargando Whisper %s en %s", self.model_name, device)
        def create(device: str, compute: str):
            try:  # sin internet si el modelo ya está descargado
                return WhisperModel(self.model_name, device=device, compute_type=compute,
                                    download_root=root, local_files_only=True)
            except Exception as e:
                if "out of memory" in str(e).lower():
                    raise
                if "incomplete" in str(e) or "failed to read" in str(e):
                    # Archivos dañados (p. ej. enlaces de la caché de Hugging Face rotos al copiar la carpeta):
                    # se borra la copia de ese modelo y se descarga de nuevo.
                    import shutil
                    log.warning("Modelo Whisper dañado (%s); se descargará de nuevo", e)
                    for d in Path(root).glob("models--*"):
                        if self.model_name.replace("/", "--").lower() in d.name.lower():
                            shutil.rmtree(d, ignore_errors=True)
                return WhisperModel(self.model_name, device=device, compute_type=compute, download_root=root)

        try:
            self._model = create(device, compute)
        except Exception as e:
            if device != "cuda":
                raise
            # La GPU puede estar llena (otro modelo cargado, p. ej. LM Studio): mejor lento que nada.
            log.warning("Whisper no cabe en la GPU (%s); usando CPU", e)
            device = "cpu"
            self._model = create("cpu", "int8")
        if device == "cuda":
            # Hay GPU NVIDIA pero puede faltar cuBLAS/cuDNN: probar y, si falla, usar CPU.
            try:
                list(self._model.transcribe(np.zeros(16000, dtype=np.float32), language="es")[0])
            except Exception as e:
                log.warning("Whisper en CUDA no disponible (%s); usando CPU", e)
                self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8", download_root=root)

    # Contexto para Whisper: con órdenes cortas ("pon Bad Bunny") sin contexto entendía
    # "Combat Bonnie" o "con la primera canción".
    PROMPT = ("Pon Bad Bunny en YouTube. Abre Spotify. Reproduce la canción. Pon la siguiente canción. "
              "Agenda una cita el viernes a las 4. ¿Cómo está el clima?")
    HALLUCINATIONS = {"gracias", "muchas gracias", "gracias por ver", "gracias por ver el video", "adiós",
                      "subtítulos realizados por la comunidad de amaraorg", "suscríbete", "música", ""}

    # Tras una pregunta de Kalmiya: sin esto, un "sí" o "no" sueltos salían como "Siv" o "9".
    ANSWER_PROMPT = "¿Lo pongo? Sí. ¿Lo abro? No. Sí, ponlo. No, mejor no. ¿Lo envío? Sí, envíalo. Mándalo."

    def transcribe(self, audio: np.ndarray, answer: bool = False) -> str:
        """`answer=True` si se espera la respuesta a una pregunta de sí o no."""
        if audio.size < 16000 * 0.3:  # menos de 0.3 s: nada que transcribir
            return ""
        self.load()
        self.confidence = 1.0
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        pad = np.zeros(int(16000 * 0.3), dtype=np.float32)  # el inicio de la frase no queda cortado
        audio = np.concatenate([pad, audio.astype(np.float32), pad])
        prompt = None
        if (self.language or "es").startswith("es"):
            prompt = self.ANSWER_PROMPT if answer else self.PROMPT

        def run(vad: bool) -> str:
            segments, _ = self._model.transcribe(
                audio, language=self.language or None, beam_size=5 if self._model.model.device == "cuda" else 1,
                vad_filter=vad, condition_on_previous_text=False, initial_prompt=prompt,
            )
            segments = list(segments)
            if segments:
                self.confidence = float(np.exp(np.mean([s.avg_logprob for s in segments])))
            return " ".join(s.text.strip() for s in segments).strip()

        text = run(vad=True)
        # El filtro de voz a veces descarta órdenes muy cortas; si hubo sonido, reintentar sin él.
        if not text and rms > 0.01:
            text = run(vad=False)
            # Sin filtro, Whisper "alucina" con ruido de fondo: frases típicas o repetir el prompt.
            if (re.sub(r"[^\wáéíóúñ ]", "", text.lower()).strip() in self.HALLUCINATIONS
                    or text.strip(" .¿?") in (prompt or "")):
                text = ""
        # Con audio casi mudo, Whisper a veces repite el prompt. Solo entonces se descarta:
        # "Pon Bad Bunny" también está en el prompt y es una orden válida.
        if rms < 0.01 and text.strip(" .") in (prompt or ""):
            text = ""
        # Con voz real Whisper sigue oyendo "Con el video de…" por "Pon el video de…";
        # una orden casi nunca empieza con "con".
        if prompt == self.PROMPT:
            text = re.sub(r"^[Cc]on(?= (el|la|los|las|un|una|algo|música|musica)\b)", "Pon", text)
        return text

    def stop(self) -> None:
        pass


class NemoTranscriber:
    """NVIDIA Nemotron (ASR 0.6B o Transcribe 30B-A4B) vía `nemo-speech serve` (NeMo-Speech.cpp).

    `model` es un alias del CLI ("nemotron-3.5") o la ruta a un GGUF; para Transcribe se
    pasa el decoder y su archivo *-Encoder-F16.gguf debe estar en la misma carpeta.
    """

    def __init__(self, model: str, language: str = "es", port: int = 8766, device: str = "auto"):
        self.model = model
        self.language = language
        self.port = port
        self.device = device
        self.proc = None
        self.client = None

    def load(self, timeout: float = 600) -> None:
        if self.client is not None:
            return
        import subprocess
        import time

        import httpx

        from .config import BIN_DIR, LOG_DIR

        from .llm import find_exe, NO_WINDOW
        exe = find_exe("nemo-speech")
        if exe is None:
            raise RuntimeError("Falta nemo-speech; reinstala con la opción de Nemotron")
        model = self.model
        if model.lower().endswith(".gguf") and not Path(model).is_absolute():
            model = str(MODELS_DIR / "nemotron" / model)
        cmd = [str(exe), "serve", "--asr-model", model, "--port", str(self.port),
               "--host", "127.0.0.1", "--device", self.device]
        log.info("Iniciando: %s", " ".join(cmd))
        logf = open(LOG_DIR / "nemo-speech.log", "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, cwd=str(exe.parent),
                                     creationflags=NO_WINDOW)
        client = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=120)
        deadline = time.time() + timeout  # la primera vez puede descargar el modelo
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("nemo-speech se cerró; revisa logs/nemo-speech.log")
            try:
                if client.get("/health", timeout=1).status_code == 200:
                    self.client = client
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError("nemo-speech no respondió a tiempo")

    def transcribe(self, audio: np.ndarray, answer: bool = False) -> str:
        if audio.size < 16000 * 0.3:
            return ""
        self.load()
        import io
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        r = self.client.post("/v1/audio/transcriptions",
                             files={"file": ("audio.wav", buf.getvalue(), "audio/wav")},
                             data={"language": self.language, "response_format": "json"})
        r.raise_for_status()
        return r.json().get("text", "").strip()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def make_transcriber(cfg) -> "Transcriber | NemoTranscriber":
    if cfg.stt_motor == "nemotron":
        return NemoTranscriber(cfg.nemo_modelo, cfg.idioma, cfg.nemo_puerto, cfg.nemo_dispositivo)
    return Transcriber(cfg.whisper_modelo, cfg.idioma)
