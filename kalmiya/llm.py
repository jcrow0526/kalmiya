"""LFM3 local vía llama-server (llama.cpp) y bucle de agente con herramientas."""
import datetime as dt
import json
import logging
import os
import re
import subprocess
import threading
import sys
import time
import uuid
from pathlib import Path

import httpx

from . import tools
from .config import BIN_DIR, LOG_DIR, MODELS_DIR, Config

log = logging.getLogger(__name__)

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

SYSTEM_PROMPT = """Eres Kalmiya, una asistente de voz que controla la computadora {sistema} del usuario.
Justo antes del mensaje del usuario recibes un bloque [Contexto] con la fecha, la hora, los próximos días y lo que tiene abierto ahora; úsalo, pero no lo repitas.
Reglas:
- Prioriza lo que el usuario tiene abierto: "esto", "este video", "esta pestaña" se refieren a la ventana activa.
- "El primer/segundo recomendado", "el video de la derecha", "el siguiente video" usan youtube_recomendado ("el siguiente video" es la posición 1).
- "Cierra esta pestaña", "regresa", "recarga", "pantalla completa", "minimiza esto" usan controlar_ventana.
- Si nombra otra pestaña ("cierra la de YouTube", "ve a la pestaña de Gmail") usa pestana, no controlar_ventana.
- "Descarga este video" usa descargar_video sin url.
- Chats o conversaciones de WhatsApp usan whatsapp_abrir_chat (la app), nunca abrir_web.
- Mandar o escribir un mensaje a alguien usa whatsapp_mensaje con las palabras exactas del usuario.
- "Pon el volumen al 50 por ciento" usa controlar_medios con fijar_volumen y nivel 50.
- Si la petición es ambigua o no tiene sentido, pregunta con responder en vez de adivinar.
- Cada petición de acción es nueva: SIEMPRE llama a la herramienta adecuada, aunque hayas hecho algo parecido antes.
- Nunca digas que hiciste algo si no llamaste la herramienta en este turno.
- Preguntas simples (cálculos, fecha, hora) se contestan con responder; datos que no sabes seguro, con buscar_info; el clima, con clima.
- "Abre mi calendario" (sin evento que crear) usa abrir_aplicacion con "calendario"; crear_evento solo si piden agendar algo.
- "¿Qué tengo hoy/mañana?", "¿tengo algo el viernes?" usan ver_agenda.
- "Recuérdame X en 20 minutos / mañana a las 9" usa recordatorio; "¿qué recordatorios tengo?" usa ver_recordatorios; "abre mis recordatorios" usa abrir_recordatorios.
- "Resúmeme las noticias", "¿qué hay de nuevo?", "noticias de deportes" usan resumen_noticias.
- "Abre la noticia de X", "abre la segunda" usan abrir_noticia (nunca abrir_web: no inventes enlaces).
- En eventos, si no dicen duración o lugar, no preguntes: usa 60 minutos y sin lugar. El título es el asunto ("Dentista"), sin verbos como "pon".
- "Pon/reproduce/quiero escuchar X" (canción, artista o video) usa reproducir: busca y lo pone a sonar. No basta con abrir YouTube.
- "Siguiente canción", "canción anterior", "pausa", "sube el volumen" usan controlar_medios sobre lo que ya suena (nunca youtube_recomendado).
- Si piden varias cosas, llama varias herramientas en la misma respuesta.
- Si nombran una ciudad, pásala siempre en el parámetro ciudad.
- Al responder con datos de buscar_info, resume en una o dos frases.
- Al resumir noticias, cuenta las 3 o 4 más importantes en frases cortas, diciendo de qué periódico es cada una.
- En fechas de eventos pasa el día tal como lo dijo el usuario ("viernes", "mañana"); solo si dice un número de día usa AAAA-MM-DD.
- Tus respuestas se leen en voz alta: responde en español, en una o dos frases cortas, sin markdown, listas ni emojis.
- No termines con preguntas de relleno como "¿algo más?".
- Si falta un dato imprescindible, pregunta brevemente."""

_SI = re.compile(r"^(yes|yeah|yep|sure|do it|send it|go ahead|please|s[ií]\w?|m[aá]ndalo|env[ií]alo|m[aá]ndale|env[ií]ale|claro|dale|ok(ay)?|va|vale|[áa]ndale|correcto|exacto|eso|ajá|aja|por favor|hazlo|"
                 r"s[ií] por favor|s[ií],? (ese|esa|eso|ponlo|[áa]brelo|ci[ée]rralo|hazlo))\b", re.I)
_NO = re.compile(r"^(no|nope|nah|cancel|don'?t|never mind|nel|nop|cancela|olv[íi]dalo|d[ée]jalo|mejor no|ninguno)\b", re.I)


_ORD = {"primer": 1, "primero": 1, "primera": 1, "segundo": 2, "segunda": 2, "tercer": 3, "tercero": 3,
        "tercera": 3, "cuarto": 4, "cuarta": 4, "quinto": 5, "quinta": 5, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
# Órdenes frecuentes y fijas: se resuelven sin el modelo (instantáneo y sin confusiones
# como "siguiente canción" -> buscar una canción llamada "La siguiente").
_QUICK = [
    (re.compile(r"^(pon |pasa a |cambia a |salta a )?(la )?(siguiente|proxima|otra) (cancion|rola|tema)$"
                r"|^(siguiente|salta(la)?|cambiala|pasala)$"), "controlar_medios", {"accion": "siguiente"}),
    (re.compile(r"^(pon |regresa a )?(la )?(cancion|rola|tema) anterior$|^anterior$"),
     "controlar_medios", {"accion": "anterior"}),
    (re.compile(r"^(pausa(la)?|pausar|para la musica|deten la musica|reanuda(la)?|continua|quita la pausa|play)$"),
     "controlar_medios", {"accion": "pausar_reanudar"}),
    # En inglés (con Kalmiya en inglés): "pause the video" acababa en canción anterior.
    (re.compile(r"^(pause|resume|unpause|stop|play)( the)?( video| music| song)?$"), "controlar_medios",
     {"accion": "pausar_reanudar"}),
    (re.compile(r"^(play )?(the )?next (song|track)$|^skip( this| the)?( song| track)?$"), "controlar_medios",
     {"accion": "siguiente"}),
    (re.compile(r"^(play )?(the )?previous (song|track)$"), "controlar_medios", {"accion": "anterior"}),
    (re.compile(r"^(pon |abre |reproduce )?(el )?(siguiente|proximo) video$"), "youtube_recomendado", {"posicion": 1}),
    (re.compile(r"^(pon|abre|reproduce|dale al?|dale|ve al?) (el |la )?(?P<n>\w+) (video |opcion )?"
                r"(de (los |las )?)?(recomendad[oa]s?|sugerid[oa]s?|de (la )?derecha)"), "youtube_recomendado",
     lambda m: {"posicion": _ORD[m["n"]]} if m["n"] in _ORD else None),
    # "Baja el volumen al 50 %": con las teclas multimedia solo se mueve de 10 en 10.
    (re.compile(r"volumen (al|a|en|hasta el|hasta)( la)? (?P<n>\d{1,3}|\w+)( por ciento)?$"), "controlar_medios",
     lambda m: {"accion": "fijar_volumen", "nivel": _numero(m["n"])} if _numero(m["n"]) is not None else None),
    # "Abre la conversación de mamá": acababa abriendo whatsapp.com en el navegador.
    (re.compile(r"^(abre|abreme|ve a|entra a|muestrame|pon) (la |el |mi )?(conversacion|chat|platica|whatsapp|mensajes)"
                r" (de|con) (?P<c>.+?)( en (el )?whatsapp)?$"), "whatsapp_abrir_chat", lambda m: {"contacto": m["c"]}),
]

_NUMEROS = {"cero": 0, "diez": 10, "veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
            "setenta": 70, "ochenta": 80, "noventa": 90, "cien": 100, "maximo": 100, "minimo": 0, "mitad": 50,
            "tope": 100}


def _numero(s: str) -> int | None:
    return int(s) if s.isdigit() else _NUMEROS.get(s)


# "Mándale a mamá que ya voy", "dile a Ana que mañana le llamo", "escríbele a Luis: llego tarde".
# Sobre el texto original (con acentos y mayúsculas): el mensaje se escribe tal como lo dictaste.
_MENSAJE = re.compile(
    r"^(?:(?:m[aá]nda(?:le)?|env[ií]a(?:le)?|escr[ií]be(?:le)?)(?: un)?(?: mensaje| whats(?:app)?| mensaje de whats(?:app)?)?"
    r"(?: por whats(?:app)?)? a|dile a|av[ií]sa(?:le)? a) (?P<c>.+?)(?: por whats(?:app)?| en whats(?:app)?)?"
    r"(?:: |,? (?:que diga|diciendo|dici[ée]ndole|que le diga|que dice|que)[:,]? )(?P<m>.+)$", re.I)


# "Pregúntale a mamá si ya comió" -> mensaje "¿Ya comió?"
_PREGUNTA = re.compile(r"^preg[uú]nta(?:le)? a (?P<c>.+?)(?: por whats(?:app)?| en whats(?:app)?)?"
                       r"(?:: |,? (?:si|que) )(?P<m>.+)$", re.I)


_TRANSLATION_EXAMPLES = [
    ("Te recordaré llamar al dentista y a mi papá en 20 minutos.",
     "I'll remind you to call the dentist and my dad in 20 minutes."),
    ("Usas 7,6 gigas de 15,6 gigas de memoria, el 49 por ciento.",
     "You're using 7.6 of 15.6 gigabytes of memory, 49 percent."),
    ("Mañana en Madrid estará despejado, con máxima de 24 grados.",
     "Tomorrow in Madrid it'll be clear, with a high of 24 degrees."),
    ("Encontré Mar Azul, de Natanael Cano. ¿Lo pongo?", "I found Mar Azul, by Natanael Cano. Should I play it?"),
]

# Respuestas fijas de las herramientas: se traducen sin el modelo (instantáneo y sin inventos).
_FIXED_EN = [
    (r"Listo", "Done."), (r"Hecho", "Done."), (r"Pantalla completa", "Full screen."),
    (r"Cerré la pestaña", "Closed the tab."), (r"Regresé", "Went back."), (r"Avancé", "Went forward."),
    (r"Recargué", "Reloaded."), (r"Siguiente", "Next."), (r"Anterior", "Previous."),
    (r"Subí el volumen", "Volume up."), (r"Bajé el volumen", "Volume down."), (r"Silenciado", "Muted."),
    (r"Volumen al (\d+) por ciento", r"Volume at \1 percent."), (r"Abrí (.+)", r"Opened \1."),
    (r"Cerré (.+)", r"Closed \1."), (r"Minimicé (.+)", r"Minimized \1."), (r"Maximicé (.+)", r"Maximized \1."),
    (r"Ahí tienes (.+)", r"Here's \1."), (r"Ahí lo tienes", "There you go."),
    (r"Pongo el tema (.+)", r"Switching to the \1 theme."), (r"Tienes el tema (.+)", r"You're using the \1 theme."),
    (r"Cambié el fondo", "Changed the wallpaper."), (r"Escritorio (\d+)", r"Workspace \1."),
    (r"Moví la ventana al escritorio (\d+)", r"Moved the window to workspace \1."),
    (r"Reproduciendo (.+)", r"Playing \1."), (r"PC bloqueada", "Computer locked."),
    (r"Tomé una captura de pantalla", "Took a screenshot."),
]


def _fixed_english(text: str) -> str | None:
    t = text.strip().rstrip(".").strip()
    for rx, en in _FIXED_EN:
        m = re.fullmatch(rx, t)
        if m:
            return m.expand(en).replace("..", ".")
    return None


def quick_route(text: str) -> tuple[str, dict] | None:
    raw = text.strip(" ¡!¿")
    m = _MENSAJE.match(raw)
    if m:
        return "whatsapp_mensaje", {"contacto": m["c"].strip(" ,"), "mensaje": m["m"].strip()}
    m = _PREGUNTA.match(raw)
    if m:
        pregunta = m["m"].strip(" .?¿")
        return "whatsapp_mensaje", {"contacto": m["c"].strip(" ,"), "mensaje": f"¿{pregunta[:1].upper()}{pregunta[1:]}?"}
    t = " ".join(tools._norm(text))
    for rx, name, args in _QUICK + tools.EXTRA_QUICK:
        m = rx.search(t)
        if m:
            if callable(args):
                args = args(m)
                if args is None:
                    continue
            return name, args
    return None


# "Más", "dime más", "siguientes": cuenta como "sí" cuando Kalmiya ofreció seguir (noticias).
_MAS = re.compile(r"^\W*(m[aá]s|dime m[aá]s|otr[oa]s|las siguientes|siguientes?|contin[uú]a|sigue|more|next)\b", re.I)


def yes_no(text: str) -> bool | None:
    """True/False si la respuesta es un sí/no claro; None si es otra petición."""
    t = text.strip(" ¡!¿?.,").lower()
    if _NO.match(t):
        return False
    if _SI.match(t):
        return True
    return None


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # solo existe en Windows


def find_exe(name: str) -> Path | None:
    """Binario descargado por el instalador en bin/ ("llama-server" o "llama-server.exe")."""
    exe = name + ".exe" if sys.platform == "win32" else name
    for p in sorted(BIN_DIR.rglob(exe)):
        if p.is_file() and (sys.platform == "win32" or os.access(p, os.X_OK)):
            return p
    return None


def server_env(exe: Path) -> dict:
    """En Linux las .so de llama.cpp van junto al binario."""
    env = dict(os.environ)
    if sys.platform != "win32":
        env["LD_LIBRARY_PATH"] = str(exe.parent) + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    return env


# Sin "pensar" en voz baja: LM Studio y otros servidores externos no reciben --chat-template-kwargs.
NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "none"}


def gpu_layers(n: int) -> list[str]:
    """99 = "todas las que quepan": se deja que llama.cpp ajuste las capas a la VRAM libre
    (con -ngl explícito no lo hace y falla si la GPU está ocupada por otro modelo)."""
    return [] if n >= 99 else ["-ngl", str(n)]


class LlamaServer:
    """Arranca y supervisa llama-server.exe con el GGUF de LFM3."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.base_url = cfg.llm_url_externa.rstrip("/") or f"http://127.0.0.1:{cfg.llm_puerto}/v1"

    @staticmethod
    def args(cfg: Config, exe, port: int, layers: list[str] | None = None) -> list[str]:
        """Comando de llama-server (también lo usa el benchmark, para medir lo mismo que se ejecuta)."""
        if layers is None:
            layers = gpu_layers(cfg.llm_gpu_capas)
        return [
            str(exe), "-m", str(MODELS_DIR / cfg.llm_gguf), "--port", str(port), "--host", "127.0.0.1",
            "-c", str(cfg.llm_contexto), *layers,
            "--jinja", "--temp", "0.1", "--top-k", "50", "--repeat-penalty", "1.1",
            "--chat-template-kwargs", '{"enable_thinking": false}',
            # LFM3 tiene capas de ventana deslizante (256 tokens): sin la caché completa, llama.cpp solo
            # retoma un punto de control 256 tokens antes de lo común y cada orden recalculaba ~515 tokens.
            "--swa-full",
        ]

    def start(self, timeout: float = 120) -> None:
        if self.cfg.llm_url_externa:
            return  # el usuario usa Ollama / LM Studio / otro servidor
        exe = find_exe("llama-server")
        model = MODELS_DIR / self.cfg.llm_gguf
        if exe is None or not model.exists():
            raise RuntimeError("Falta llama-server o el modelo LFM3. Ejecuta el instalador de nuevo.")
        try:
            self._launch(exe, gpu_layers(self.cfg.llm_gpu_capas), timeout)
        except RuntimeError:
            if self.cfg.llm_gpu_capas == 0:
                raise
            # La GPU puede estar ocupada por otro modelo (LM Studio, un juego…): mejor en CPU que nada.
            log.warning("LFM3 no cabe en la GPU; usando la CPU (revisa logs/llama-server.log)")
            self.stop()
            self._launch(exe, ["-ngl", "0", "--device", "none"], timeout)

    def _launch(self, exe: Path, layers: list[str], timeout: float) -> None:
        cmd = self.args(self.cfg, exe, self.cfg.llm_puerto, layers)
        log.info("Iniciando: %s", " ".join(cmd))
        logf = open(LOG_DIR / "llama-server.log", "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            cmd, stdout=logf, stderr=subprocess.STDOUT, cwd=str(exe.parent),
            creationflags=NO_WINDOW, env=server_env(exe),
        )
        url = f"http://127.0.0.1:{self.cfg.llm_puerto}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("llama-server se cerró; revisa logs/llama-server.log")
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("llama-server no respondió a tiempo")
        # Una respuesta de prueba: sin VRAM suficiente el servidor arranca y se cae al primer cálculo.
        try:
            httpx.post(f"{url}/v1/chat/completions", timeout=60, json={
                "messages": [{"role": "user", "content": "hola"}], "max_tokens": 1}).raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(f"llama-server falló al responder: {e}") from e

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# LFM3 emite llamadas en XML. llama.cpp normalmente las convierte en `tool_calls`,
# pero si la versión del servidor no las reconoce, las extraemos del texto.
_TOOL_XML = re.compile(r"<tool_call>\s*<function=([\w\-.]+)>(.*?)</function>\s*</tool_call>", re.S)
_PARAM_XML = re.compile(r"<parameter=([\w\-.]+)>\s*(.*?)\s*</parameter>", re.S)


def _coerce(value: str):
    v = value.strip()
    try:
        return json.loads(v)
    except (json.JSONDecodeError, ValueError):
        return v


def parse_xml_tool_calls(content: str) -> tuple[str, list[dict]]:
    calls = []
    for m in _TOOL_XML.finditer(content or ""):
        args = {k: _coerce(v) for k, v in _PARAM_XML.findall(m.group(2))}
        calls.append({
            "id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
            "function": {"name": m.group(1), "arguments": json.dumps(args, ensure_ascii=False)},
        })
    return _TOOL_XML.sub("", content or "").strip(), calls


def strip_think(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    return text.split("</think>")[-1].strip()


# El modelo pequeño tiende a rematar con "¿Algo más?" pese al prompt; se quita para hablar menos.
_FILLER = re.compile(r"\s*¿[^?¿]*(algo más|otra cosa|sigue algo|qué (más )?(buscas|necesitas|quieres)[^?]*"
                     r"|(te interesa|quieres|deseas)[^?]*(más detalles|profundice|más informaci[oó]n|sepa más)[^?]*)\?\s*$",
                     re.I)


def short(text: str, max_sentences: int = 2) -> str:
    """Para voz: como mucho dos frases (los resúmenes de Wikipedia salen largos)."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡])", text.strip())
    return " ".join(parts[:max_sentences])


def calls_made(turn: list[dict]) -> bool:
    return any(m["role"] == "tool" for m in turn)


def strip_filler(text: str) -> str:
    text = re.sub(r"\*\*|__|^#+\s*", "", text, flags=re.M)  # sin negritas ni títulos de markdown
    stripped = _FILLER.sub("", text).strip()
    return stripped or text


class Agent:
    def __init__(self, base_url: str, max_steps: int = 5, memory_turns: int = 2, fast: bool = True,
                 english: bool = False):
        self.client = httpx.Client(base_url=base_url, timeout=120)
        self.fast = fast
        # En inglés las herramientas siguen respondiendo en español; se traduce la respuesta final.
        self.english = english
        self.max_steps = max_steps
        self.memory_turns = memory_turns
        self.turns: list[list[dict]] = []  # turnos recientes completos (sin system)
        self.pending = None  # acción que espera confirmación (ver tools.ask)
        self.asked = False  # la última respuesta fue una pregunta
        self._anchor_n = 0
        self.anchor()

    def anchor(self) -> None:
        """Deja en llama-server un punto de control justo al final de la parte fija (sistema +
        herramientas). LFM3 es híbrido: su caché solo se retoma desde puntos de control, y llama.cpp
        los crea al empezar el último mensaje, ~515 tokens antes del final y donde retomó. Una orden
        sin contexto crea el primero; después cada orden lo vuelve a guardar al retomarlo. Así se
        recalculan solo el contexto y la orden (~160 tokens) en vez de ~515 (0.5 s en una GTX 1660)."""
        try:
            r = self.client.post("/chat/completions", json={
                **self._payload([self._system(), {"role": "user", "content": "hola"}]), "max_tokens": 1})
            r.raise_for_status()
            tm = r.json().get("timings") or {}
            self._anchor_n = tm.get("prompt_n", 0) + tm.get("cache_n", 0)
        except Exception as e:
            log.warning("No pude preparar la caché de LFM3: %s", e)

    def _system(self) -> dict:
        # Fijo: llama-server reutiliza de su caché el sistema + los ~3400 tokens de herramientas.
        # Con la hora o la ventana activa aquí, cada orden recalculaba todo (~6 s en una GTX 1660).
        sistema = "Windows" if sys.platform == "win32" else ("Linux (Omarchy)" if tools.EXTRA_RULES else "Linux")
        return {"role": "system", "content": SYSTEM_PROMPT.format(sistema=sistema) + tools.EXTRA_RULES}

    @staticmethod
    def _context(contexto: str) -> dict:
        """Fecha, hora y ventana activa en un mensaje de sistema aparte, justo antes del turno actual.
        Dentro del mensaje del usuario, el modelo tomaba el título de la ventana como lo que pedía
        ("cierra esta pestaña" -> cerrar la pestaña "Descargas")."""
        now = dt.datetime.now()
        # El modelo pequeño falla con aritmética de fechas ("el viernes"): se la damos hecha.
        dias = [now + dt.timedelta(days=i) for i in range(1, 8)]
        semana = ", ".join(
            ("mañana " if i == 0 else "") + f"{DIAS[d.weekday()]} {d:%Y-%m-%d}" for i, d in enumerate(dias))
        bloque = (f"[Contexto]\nFecha y hora actual: {now:%Y-%m-%d %H:%M} ({DIAS[now.weekday()]}).\n"
                  f"Próximos días: {semana}.\nLo que el usuario tiene abierto ahora:\n{contexto or '(desconocido)'}\n"
                  f"[Fin del contexto]")
        return {"role": "system", "content": bloque}

    def _payload(self, messages: list[dict]) -> dict:
        return {"model": "lfm3", "messages": messages, "tools": tools.schemas(),
                "temperature": 0.1, "top_k": 50, "repeat_penalty": 1.1, "max_tokens": 512, **NO_THINKING}

    def _chat(self, messages: list[dict]) -> dict:
        r = self.client.post("/chat/completions", json=self._payload(messages))
        r.raise_for_status()
        data = r.json()
        self._check_cache(data.get("timings") or {})
        return data["choices"][0]["message"]

    def _check_cache(self, timings: dict) -> None:
        """Si el punto de control de la parte fija se perdió (llama.cpp guarda 32 por ranura), se
        vuelve a crear en segundo plano para que la siguiente orden no recalcule de más."""
        if self._anchor_n and timings.get("cache_n", self._anchor_n) < self._anchor_n - 16:
            threading.Thread(target=self.anchor, daemon=True).start()

    def run(self, user_text: str, on_tool=None, contexto: str = "") -> str:
        reply = self._run(user_text, on_tool, contexto)
        if self.english and reply:
            reply = self.to_english(reply)
        self.asked = reply.rstrip().endswith("?")
        return reply

    def to_english(self, text: str) -> str:
        text = re.sub(r"[*_`#]+", "", text).strip()
        fixed = _fixed_english(text)
        if fixed is not None:
            return fixed
        try:
            # Con ejemplos: sin ellos LFM3 explicaba las frases cortas ("Listo" is Spanish for…) o se
            # ponía a hablar del tema ("Kanagawa is a region in Japan…") en vez de traducir.
            messages = [{"role": "system", "content": "You translate Spanish to English. Reply with the English "
                                                      "translation only: no notes, no quotes, no markdown. Keep "
                                                      "names, song and video titles unchanged."}]
            for es, en in _TRANSLATION_EXAMPLES:
                messages += [{"role": "user", "content": es}, {"role": "assistant", "content": en}]
            messages.append({"role": "user", "content": text})
            r = self.client.post("/chat/completions", json={
                "model": "lfm3", "temperature": 0.0, "max_tokens": 200, **NO_THINKING, "messages": messages})
            r.raise_for_status()
            out = strip_think(r.json()["choices"][0]["message"].get("content") or "")
            out = re.sub(r"[*_`#]+", "", out).strip().strip('"').strip()
            # "Te recordaré…" = "I'll remind you…" (el modelo traduce "I'll remember…").
            out = re.sub(r"\bI'?ll remember (?:you )?to\b", "I'll remind you to", out)
            # Una "traducción" mucho más larga que el original es una explicación, no una traducción.
            if not out or len(out) > 2.2 * len(text) + 40:
                return text
            return out
        except Exception:
            log.exception("No pude traducir la respuesta")
            return text

    def _run(self, user_text: str, on_tool, contexto: str) -> str:
        answer = yes_no(user_text)
        short_answer = len(user_text.split()) <= 3
        if self.pending is not None:
            accion, self.pending = self.pending, None
            if answer is True or (answer is None and _MAS.search(user_text)):
                tools.ctx.pending = None
                out = accion()
                # La acción puede volver a preguntar (p.ej. las noticias: "¿quieres saber más?").
                self.pending, tools.ctx.pending = tools.ctx.pending, None
                return out
            if answer is False:
                cancelado = accion.cancel() if hasattr(accion, "cancel") else "Vale, no lo hago."
                if short_answer:
                    return cancelado
            # "No, mejor pon Shakira": se cancela y se atiende la nueva petición.
        elif self.asked and answer is False and short_answer:
            # "No" a una pregunta del modelo: el modelo pequeño a veces hacía justo lo que ofrecía.
            return "Vale."
        elif not self.asked and answer is not None and len(user_text.split()) <= 2:
            # "No" / "sí" sueltos sin nada pendiente: con historial el modelo repetía la última acción.
            return "Vale."
        tools.ctx.pending = None
        turn = [{"role": "user", "content": user_text}]
        quick = quick_route(user_text) or tools.tab_intent(user_text) or tools.news_intent(user_text)
        if quick:
            name, args = quick
            log.info("Herramienta %s(%s) [directa]", name, args)
            if on_tool:
                on_tool(name, args)
            result = tools.call(name, args)
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            turn += [{"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function",
                      "function": {"name": name, "arguments": json.dumps(args)}}]},
                     {"role": "tool", "tool_call_id": call_id, "content": result},
                     {"role": "assistant", "content": result}]
            self._remember(turn)
            if tools.ctx.pending is not None:  # la herramienta preguntó ("¿quieres saber más?")
                self.pending, tools.ctx.pending = tools.ctx.pending, None
            return result
        for _ in range(self.max_steps):
            msg = self._chat([self._system(), *self.history, self._context(contexto), *turn])
            content = strip_think(msg.get("content") or "")
            calls = msg.get("tool_calls") or []
            if not calls:
                content, calls = parse_xml_tool_calls(content)
            if not calls:
                if not content:  # el modelo calló tras usar herramientas: leer el último resultado
                    content = next((m["content"] for m in reversed(turn) if m["role"] == "tool"), "")
                turn.append({"role": "assistant", "content": content})
                self._remember(turn)
                if not calls_made(turn):
                    return content
                # Un resumen de noticias necesita más de dos frases.
                noticias = any(m.get("name") == "resumen_noticias" or "resumen_noticias" in str(m.get("tool_calls", ""))
                               for m in turn)
                return short(strip_filler(content), 6 if noticias else 2)
            calls = tools.prune(calls, user_text)
            turn.append({"role": "assistant", "content": content, "tool_calls": calls})
            results = []
            for c in calls:
                name = c["function"]["name"]
                raw = c["function"].get("arguments") or "{}"
                args = json.loads(raw) if isinstance(raw, str) else raw
                log.info("Herramienta %s(%s)", name, args)
                if on_tool:
                    on_tool(name, args)
                result = tools.call(name, args)
                results.append(result)
                turn.append({"role": "tool", "tool_call_id": c.get("id", name), "content": result})
            if tools.ctx.pending is not None:
                # Una herramienta no estaba segura y preguntó: esperar la respuesta del usuario.
                self.pending, tools.ctx.pending = tools.ctx.pending, None
                reply = ". ".join(r.rstrip(".") for r in results if not r.endswith("?"))
                reply = (reply + ". " if reply else "") + next(r for r in results if r.endswith("?"))
                turn.append({"role": "assistant", "content": reply})
                self._remember(turn)
                return reply
            needs_model = any(not tools.TOOLS.get(c["function"]["name"], {}).get("direct", True) for c in calls)
            if self.fast and not needs_model and not any(r.startswith("Error") for r in results):
                # Modo rápido: la confirmación de la herramienta ya está bien redactada;
                # nos ahorramos otra pasada del modelo (menos latencia, sin faltas ni relleno).
                reply = ". ".join(r.rstrip(".") for r in results)
                reply += "" if reply.endswith(("?", "!")) else "."
                turn.append({"role": "assistant", "content": reply})
                self._remember(turn)
                return reply
        self._remember(turn)
        return "Listo."

    def _remember(self, turn: list[dict]) -> None:
        # Guardamos el turno completo (con sus tool_calls): si el historial solo tuviera
        # respuestas de texto, el modelo aprende a "decir" que hizo algo sin llamar herramientas.
        self.turns.append(turn)
        self.turns = self.turns[-self.memory_turns:]

    @property
    def history(self) -> list[dict]:
        return [m for t in self.turns for m in t]

    def reset(self) -> None:
        self.turns.clear()
        self.pending = None
        self.asked = False
