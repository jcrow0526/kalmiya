"""Rutas y configuración persistente de Kalmiya."""
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "Kalmiya"

# Todo lo de la app vive en %LOCALAPPDATA%\Kalmiya (modelos, binarios, logs, config);
# en Linux, en ~/.local/share/kalmiya.
if sys.platform == "win32":
    _LOCAL = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    HOME = Path(os.environ.get("KALMIYA_HOME") or _LOCAL / APP_NAME)
else:
    _LOCAL = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    HOME = Path(os.environ.get("KALMIYA_HOME") or _LOCAL / APP_NAME.lower())
if not os.environ.get("KALMIYA_HOME") and not HOME.exists() and (_LOCAL / "Eco" / "models").exists():
    HOME = _LOCAL / "Eco"  # instalación anterior (cuando se llamaba Eco) aún sin migrar
MODELS_DIR = HOME / "models"
BIN_DIR = HOME / "bin"
LOG_DIR = HOME / "logs"
CONFIG_FILE = HOME / "config.json"
CALENDAR_FILE = HOME / "calendario.json"
GOOGLE_CLIENT_FILE = HOME / "google_client.json"  # credenciales OAuth "app de escritorio" del usuario
GOOGLE_TOKEN_FILE = HOME / "google_token.json"

USER = Path.home()
DOWNLOADS_DIR = USER / "Videos" / "Kalmiya"
EVENTS_DIR = USER / "Documents" / "Kalmiya" / "eventos"


@dataclass
class Config:
    # Atajos (sintaxis de la librería `keyboard`; en Linux se registran como binds de Hyprland).
    hotkey_agente: str = "ctrl+alt+space"
    hotkey_dictado: str = "ctrl+alt+d"
    hotkey_nueva: str = "ctrl+alt+n"  # olvidar la conversación y empezar otra
    hotkey_imagen: str = "ctrl+alt+i"  # clic en una imagen de la pantalla y preguntar por ella
    modo_hotkey: str = "mantener"  # "mantener" (push-to-talk) o "alternar"

    # Voz a texto
    stt_motor: str = "whisper"  # whisper | nemotron
    # large-v3-turbo en GPU NVIDIA (~0.2 s por frase); en CPU el instalador elige "small".
    whisper_modelo: str = "large-v3-turbo"  # tiny | base | small | medium | large-v3-turbo
    # Nemotron: alias "nemotron-3.5" (ASR 0.6B, se descarga solo) o un GGUF en models\nemotron
    # como "NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-AD-IQ4_NL.gguf".
    nemo_modelo: str = "nemotron-3.5"
    nemo_puerto: int = 8766
    nemo_dispositivo: str = "auto"  # auto | cpu | cuda | vulkan
    idioma: str = "es"
    microfono: int | None = None  # índice de sounddevice; None = predeterminado

    # LLM local
    llm_gguf: str = "LFM3-1.7B-Q4_K_M.gguf"
    llm_puerto: int = 8765
    llm_contexto: int = 8192
    llm_gpu_capas: int = 99  # se ignora si el binario es solo-CPU
    llm_url_externa: str = ""  # p.ej. http://localhost:11434/v1 para usar Ollama/LM Studio

    # Texto a voz
    responder_con_voz: bool = True
    piper_voz: str = "es_AR-daniela-high.onnx"  # español latino, mujer (en inglés: en_US-lessac-high)
    velocidad_voz: float = 1.0

    # Apariencia
    color_orbe: str = "#7CFF2B"
    primera_vez: bool = True  # mostrar la presentación al arrancar

    # Visión (preguntar por imágenes): LFM3-VL en otro llama-server, se arranca al primer uso
    vl_gguf: str = "LFM3-VL-2B-Q4_K_M.gguf"
    vl_mmproj: str = "mmproj-LFM3-VL-2B-Q8_0.gguf"
    vl_puerto: int = 8767

    # Herramientas
    # kalmiya = calendario propio de Kalmiya (y además Google si está conectado) | outlook | ics
    calendario: str = "kalmiya"
    ciudad: str = ""  # ciudad por defecto para el clima
    # Preguntar antes de actuar si no está claro qué quiso decir el usuario
    # (voz mal entendida, resultado que no se parece, app con nombre aproximado).
    confirmar_si_dudo: bool = True
    escuchar_respuesta: bool = True  # tras una pregunta, escuchar la respuesta sin pulsar el atajo
    bajar_volumen: bool = True  # bajar las demás apps mientras hablas con Kalmiya

    # Periódicos (RSS) para "resúmeme las noticias": [{"nombre": ..., "url": ...}]
    noticias: list = field(default_factory=list)

    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
                if cfg.calendario in ("auto", "google", "eco"):  # opciones de versiones anteriores
                    cfg.calendario = "kalmiya"
            except Exception:
                pass
        return cfg

    def save(self) -> None:
        HOME.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")


def ensure_dirs() -> None:
    for d in (HOME, MODELS_DIR, BIN_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
