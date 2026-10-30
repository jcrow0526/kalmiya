"""Benchmark de Kalmiya: mide voz a texto, LFM3, voz y visión en este equipo.

    python -m kalmiya.bench               # completo (~2-4 min)
    python -m kalmiya.bench --quick       # menos frases, sin visión
    python -m kalmiya.bench --submit      # además abre GitHub para compartir el resultado

Escribe Documentos/Kalmiya/benchmark/kalmiya-bench-<equipo>-<fecha>.json y .md. El formato está
descrito en bench/README.md del repositorio. Las herramientas se simulan: no abre ni toca nada en la PC.
"""
import argparse
import datetime as dt
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

import httpx
import numpy as np

from . import __version__, llm, tools
from .config import BIN_DIR, MODELS_DIR, Config

SCHEMA = 1
REPO = "https://github.com/jcrow0526/kalmiya"

# Frases para voz a texto: se sintetizan con la voz de Kalmiya, así todos miden lo mismo.
STT_PHRASES = {
    "es": ["Pon Bad Bunny en YouTube.", "¿Cómo está el clima mañana en Madrid?",
           "Agéndame dentista el viernes a las cuatro de la tarde.", "Cierra la pestaña de YouTube.",
           "Recuérdame sacar la ropa en veinte minutos.", "Resúmeme las noticias de tecnología.",
           "Abre el primer video recomendado.", "Dile a Ana que ya voy en camino."],
    "en": ["Play Bad Bunny on YouTube.", "What's the weather like tomorrow in Madrid?",
           "Remind me to do the laundry in twenty minutes.", "Close the YouTube tab."],
}

# Órdenes y herramienta esperada (la primera que debe llamar). Varias válidas separadas por "|".
LLM_SUITE = [
    ("Pon Bad Bunny", "reproducir"),
    ("Ponme un video de cómo hacer tacos al pastor", "reproducir"),
    ("Quiero escuchar Shakira en Spotify", "reproducir"),
    ("¿Cómo está el clima en Guadalajara?", "clima"),
    ("¿Va a llover mañana en Madrid?", "clima"),
    ("¿Quién es Bad Bunny?", "buscar_info"),
    ("¿Cuánto es 20 por 8?", "responder|-"),  # "-": contestar sin herramienta también vale
    ("Ponme dentista el viernes a las 4", "crear_evento"),
    ("¿Qué tengo mañana?", "ver_agenda"),
    ("Recuérdame sacar la ropa en 20 minutos", "recordatorio"),
    ("¿Qué recordatorios tengo?", "ver_recordatorios"),
    ("Abre Spotify", "abrir_aplicacion"),
    ("Cierra la calculadora", "cerrar_aplicacion"),
    ("Busca recetas de mole en Google", "buscar_web"),
    ("Abre youtube.com", "abrir_web|abrir_aplicacion"),
    ("Descarga el video de Dákiti en mp3", "descargar_video"),
    ("Sube el volumen", "controlar_medios"),
    ("Toma una captura de pantalla", "captura_pantalla"),
    ("Abre la carpeta de descargas", "abrir_carpeta"),
    ("Escríbele a Ana que ya voy", "whatsapp_mensaje"),
    ("Minimiza esto", "controlar_ventana"),
    ("Resúmeme las noticias", "resumen_noticias"),
    ("Siguiente canción", "controlar_medios"),
    ("Cierra la pestaña de YouTube", "pestana"),
]


def pct(xs: list[float], p: float) -> float:
    return float(np.percentile(xs, p)) if xs else float("nan")


def summary(xs: list[float]) -> dict:
    return {"p50": round(pct(xs, 50), 3), "p90": round(pct(xs, 90), 3), "mean": round(statistics.mean(xs), 3),
            "n": len(xs)} if xs else {}


_NUM = {"uno": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5", "diez": "10", "veinte": "20",
        "one": "1", "two": "2", "four": "4", "twenty": "20"}


def wer(ref: str, hyp: str) -> float:
    """Tasa de error por palabra; "veinte" y "20" cuentan igual (Whisper escribe los números con cifras)."""
    norm = lambda s: [_NUM.get(w, w) for w in re.findall(r"[a-z0-9áéíóúñü]+", s.lower())]
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)] / max(1, len(r))


# ------------------------------------------------------------------ equipo

def system_info(cfg: Config) -> dict:
    import psutil
    info = {"os": f"{platform.system()} {platform.release()} ({platform.version()})",
            "cpu": platform.processor(), "cores": psutil.cpu_count(logical=False),
            "threads": psutil.cpu_count(), "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
            "gpu": None, "vram_gb": None, "gpu_driver": None}
    try:
        if sys.platform == "win32":
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            info["cpu"] = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        else:  # platform.processor() en Linux solo dice "x86_64"
            cpu = re.search(r"^model name\s*:\s*(.+)$", Path("/proc/cpuinfo").read_text(), re.M)
            info["cpu"] = cpu.group(1).strip() if cpu else info["cpu"]
            os_name = re.search(r'^PRETTY_NAME="?([^"\n]+)', Path("/etc/os-release").read_text(), re.M)
            info["os"] = f"{os_name.group(1) if os_name else 'Linux'} (kernel {platform.release()})"
    except Exception:
        pass
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, creationflags=llm.NO_WINDOW).stdout
        name, mem, drv = [x.strip() for x in out.splitlines()[0].split(",")]
        info.update(gpu=name, vram_gb=round(int(mem) / 1024, 1), gpu_driver=drv)
    except Exception:
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name"],
                                 capture_output=True, text=True, timeout=15, creationflags=llm.NO_WINDOW).stdout
            info["gpu"] = out.strip() or None
        except Exception:
            pass
    version = BIN_DIR / "llama" / "VERSION"
    info["llama_cpp"] = version.read_text().strip() if version.exists() else None
    info["kalmiya"] = __version__
    info["llm_model"] = cfg.llm_gguf
    info["whisper_model"] = cfg.whisper_modelo
    info["voice"] = cfg.piper_voz
    return info


# ------------------------------------------------------------------ voz (TTS)

def bench_tts(cfg: Config, log) -> dict:
    from .tts import Speaker
    out = {}
    for lang, voice, short, long in (
            ("es", "es_AR-daniela-high.onnx", "Listo, te recordaré sacar la ropa en veinte minutos.",
             "De El Universal: gobernadores lamentan el fallecimiento de Cuauhtémoc Cárdenas. De Xataka: en Florida "
             "ya hay policías que patrullan en dron. De ESPN: los Brewers ganan su primera Serie Mundial. "
             "¿Quieres saber más?"),
            ("en", "en_US-lessac-high.onnx", "Done, I'll remind you to do the laundry in twenty minutes.", "")):
        if not (MODELS_DIR / "piper" / voice).exists():
            continue
        sp = Speaker(voice)
        t = time.perf_counter()
        sp.warmup()  # lo mismo que hace Kalmiya al arrancar
        load = time.perf_counter() - t
        res = {"load_s": round(load, 3)}
        for name, text in (("short", short), ("long", long)):
            if not text:
                continue
            # Mismo camino que al hablar: fragmentos que se reproducen en cuanto están listos.
            first = None
            t = time.perf_counter()
            parts, sr = [], 22050
            for a, sr in sp.chunks(text):
                if first is None:
                    first = time.perf_counter() - t
                parts.append(a)
            total = time.perf_counter() - t
            dur = sum(len(p) for p in parts) / sr
            res[name] = {"synth_s": round(total, 3), "first_chunk_s": round(first, 3), "audio_s": round(dur, 2),
                         "rtf": round(total / dur, 3)}
        out[lang] = res
        log(f"   voz {lang}: corta {res['short']['synth_s']:.2f} s, RTF {res['short']['rtf']:.2f}")
    return out


# ------------------------------------------------------------------ voz a texto (STT)

def bench_stt(cfg: Config, quick: bool, log) -> dict:
    from .stt import Transcriber
    from .tts import Speaker
    t = time.perf_counter()
    st = Transcriber(cfg.whisper_modelo, "es")
    st.load()
    load = time.perf_counter() - t
    device = str(st._model.model.device)
    log(f"   Whisper {cfg.whisper_modelo} en {device}: cargado en {load:.1f} s")
    res = {"model": cfg.whisper_modelo, "device": device, "load_s": round(load, 2), "langs": {}}
    for lang, voice in (("es", "es_AR-daniela-high.onnx"), ("en", "en_US-lessac-high.onnx")):
        if not (MODELS_DIR / "piper" / voice).exists():
            continue
        sp = Speaker(voice)
        st.language = lang
        phrases = STT_PHRASES[lang][:4 if quick else None]
        lat, wers, items = [], [], []
        st.transcribe(np.zeros(16000, np.float32) + 1e-4)  # calentar
        for p in phrases:
            a, sr = sp._piper_audio(p)
            clip = np.interp(np.arange(0, len(a), sr / 16000), np.arange(len(a)), a).astype(np.float32)
            t = time.perf_counter()
            text = st.transcribe(clip)
            dt_ = time.perf_counter() - t
            w = wer(p, text)
            lat.append(dt_)
            wers.append(w)
            items.append({"ref": p, "hyp": text, "s": round(dt_, 3), "wer": round(w, 3),
                          "audio_s": round(len(clip) / 16000, 2)})
        res["langs"][lang] = {"latency": summary(lat), "wer_mean": round(statistics.mean(wers), 3), "items": items}
        log(f"   STT {lang}: p50 {pct(lat, 50):.2f} s, WER {statistics.mean(wers):.1%}")
    st._model = None
    return res


# ------------------------------------------------------------------ LFM3

class TimedAgent(llm.Agent):
    """Agent que guarda los tiempos internos de llama-server de cada petición."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.timings: list[dict] = []

    def _chat(self, messages):
        r = self.client.post("/chat/completions", json=self._payload(messages))
        r.raise_for_status()
        data = r.json()
        self.timings.append(data.get("timings") or {})
        return data["choices"][0]["message"]


def start_server(cfg: Config, port: int, out_dir: Path, log, own: bool = False,
                 extra: list[str] | None = None) -> tuple[subprocess.Popen | None, str, float, bool]:
    """Usa el llama-server de Kalmiya si ya está abierto; si no (o con `own`), arranca uno propio
    con los mismos argumentos que Kalmiya (más `extra`, para probar ajustes)."""
    url = f"http://127.0.0.1:{cfg.llm_puerto}"
    try:
        if not own and httpx.get(url + "/health", timeout=1).status_code == 200:
            log("   usando el servidor de LFM3 que ya tiene Kalmiya abierto")
            return None, url + "/v1", 0.0, True
    except httpx.HTTPError:
        pass
    exe = llm.find_exe("llama-server")
    if exe is None:
        raise RuntimeError("Falta llama-server; ejecuta el instalador de nuevo")
    cmd = llm.LlamaServer.args(cfg, exe, port) + (extra or [])
    logf = open(out_dir / "llama-server-bench.log", "w", encoding="utf-8", errors="replace")
    t = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, cwd=str(exe.parent),
                            creationflags=llm.NO_WINDOW, env=llm.server_env(exe))
    url = f"http://127.0.0.1:{port}"
    while True:
        if proc.poll() is not None:
            raise RuntimeError("llama-server se cerró; revisa llama-server-bench.log")
        try:
            if httpx.get(url + "/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    load = time.perf_counter() - t
    log(f"   LFM3 cargado en {load:.1f} s")
    return proc, url + "/v1", load, False


def bench_llm(cfg: Config, port: int, out_dir: Path, log, own: bool = False, extra: list[str] | None = None,
              repeat: int = 1) -> dict:
    proc, base, load, shared = start_server(cfg, port, out_dir, log, own, extra)
    try:
        # Herramientas simuladas: solo se registra cuál llamó.
        called: list[str] = []
        real = {n: t["fn"] for n, t in tools.TOOLS.items()}
        for n in tools.TOOLS:
            tools.TOOLS[n]["fn"] = (lambda n=n: (lambda **kw: called.append(n) or "Hecho."))()
        tools.ctx.config = cfg
        ctxt = "Ventana activa: Chrome — \"YouTube\" (https://www.youtube.com/watch?v=dQw4w9WgXcQ)\n" \
               "Otras apps abiertas: Spotify, WhatsApp"
        agent = TimedAgent(base)
        agent.run("Hola", contexto=ctxt)  # calentar
        lat, ok, items, direct = [], 0, [], 0
        for text, expected in LLM_SUITE * repeat:
            called.clear()
            agent.reset()
            agent.timings.clear()
            t = time.perf_counter()
            reply = agent.run(text, contexto=ctxt)
            dt_ = time.perf_counter() - t
            first = called[0] if called else None
            good = (first or "-") in expected.split("|")
            ok += good
            if not agent.timings:
                direct += 1  # resuelto por reglas, sin llamar al modelo
            lat.append(dt_)
            tm = agent.timings[0] if agent.timings else {}
            items.append({"text": text, "expected": expected, "called": first, "ok": good, "s": round(dt_, 3),
                          "llm_calls": len(agent.timings), "prompt_n": tm.get("prompt_n"),
                          "cache_n": tm.get("cache_n"), "prompt_ms": tm.get("prompt_ms"),
                          "predicted_n": tm.get("predicted_n"), "predicted_ms": tm.get("predicted_ms"),
                          "reply": reply[:120]})
        for n, fn in real.items():
            tools.TOOLS[n]["fn"] = fn
        # Velocidad pura del modelo: lectura de un prompt largo y generación de texto.
        c = httpx.Client(base_url=base, timeout=120)
        long_prompt = "Resume este texto en una frase. " + " ".join(["Kalmiya es una asistente de voz local."] * 120)
        c.post("/chat/completions", json={"messages": [{"role": "user", "content": "Hola"}], "max_tokens": 4})
        r = c.post("/chat/completions", json={"messages": [{"role": "user", "content": long_prompt}],
                                              "max_tokens": 128, "temperature": 0, "cache_prompt": False}).json()
        tm = r.get("timings") or {}
        speed = {"prompt_tok_s": round(tm.get("prompt_per_second", 0), 1),
                 "gen_tok_s": round(tm.get("predicted_per_second", 0), 1),
                 "prompt_n": tm.get("prompt_n"), "predicted_n": tm.get("predicted_n")}
        model_items = [i for i in items if i["prompt_n"] is not None]
        total = len(LLM_SUITE) * repeat
        res = {"shared_server": shared, "load_s": round(load, 2), "accuracy": round(ok / total, 3),
               "correct": ok, "total": total, "resolved_by_rules": direct,
               "cache_misses": sum(1 for i in model_items if (i["prompt_n"] or 0) > 1500),
               "latency": summary(lat), "latency_model_only": summary([i["s"] for i in model_items]),
               "prompt_tokens_p50": pct([i["prompt_n"] for i in model_items], 50) if model_items else None,
               "cached_tokens_p50": pct([i["cache_n"] or 0 for i in model_items], 50) if model_items else None,
               "speed": speed, "items": items}
        log(f"   LFM3: {ok}/{total} herramientas correctas, p50 {pct(lat, 50):.2f} s, "
            f"caché fallida {res['cache_misses']}/{len(model_items)}, "
            f"lectura {speed['prompt_tok_s']:.0f} tok/s, generación {speed['gen_tok_s']:.0f} tok/s")
        return res
    finally:
        if proc:
            proc.terminate()


# ------------------------------------------------------------------ visión

def _bold_font(size: int):
    from PIL import ImageFont
    if sys.platform == "win32":
        return ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", size)
    try:  # la sans negrita que tenga la distribución (Liberation, DejaVu, Noto…)
        f = subprocess.run(["fc-match", "-f", "%{file}", "sans:bold"], capture_output=True, text=True, timeout=5).stdout
        return ImageFont.truetype(f, size)
    except Exception:
        return ImageFont.load_default(size)


def bench_vision(cfg: Config, log) -> dict:
    from PIL import Image, ImageDraw, ImageFont
    from .vision import VisionServer
    if not (MODELS_DIR / cfg.vl_gguf).exists():
        return {"skipped": "modelo de visión no instalado"}
    cfg.vl_puerto = cfg.vl_puerto + 10  # no chocar con el de Kalmiya
    vs = VisionServer(cfg)
    try:
        t = time.perf_counter()
        vs.start()
        load = time.perf_counter() - t
        img = Image.new("RGB", (640, 360), (250, 250, 250))
        d = ImageDraw.Draw(img)
        d.ellipse((60, 90, 240, 270), fill=(220, 30, 40))
        d.text((300, 150), "KALMIYA", fill=(10, 10, 10), font=_bold_font(64))
        lat, answers = [], []
        for q in ("¿Qué palabra está escrita en la imagen?", "¿De qué color es el círculo?"):
            t = time.perf_counter()
            a = vs.ask(img, q)
            lat.append(time.perf_counter() - t)
            answers.append(a)
        ok = ["kalmiya" in answers[0].lower(), "roj" in answers[1].lower()]
        log(f"   visión: cargada en {load:.1f} s, respuesta p50 {pct(lat, 50):.2f} s, {sum(ok)}/2 correctas")
        return {"load_s": round(load, 2), "latency": summary(lat), "correct": sum(ok), "total": 2,
                "answers": answers}
    finally:
        vs.stop()


# ------------------------------------------------------------------ informe

def to_markdown(r: dict) -> str:
    s, m = r["system"], []
    m.append(f"## Kalmiya {s['kalmiya']} · {s['gpu'] or 'sin GPU'} · {s['cpu']}")
    m.append(f"{s['os']} · {s['ram_gb']} GB RAM · VRAM {s['vram_gb'] or '-'} GB · llama.cpp {s['llama_cpp']}\n")
    m.append("| Etapa | Resultado |\n|---|---|")
    if "stt" in r:
        st = r["stt"]
        for lang, v in st["langs"].items():
            m.append(f"| Voz a texto {lang} ({st['model']}, {st['device']}) | p50 **{v['latency']['p50']:.2f} s** · "
                     f"WER {v['wer_mean']:.1%} |")
    if "llm" in r:
        l = r["llm"]
        m.append(f"| LFM3 elige la herramienta correcta | **{l['correct']}/{l['total']}** "
                 f"({l['resolved_by_rules']} por reglas directas) |")
        m.append(f"| LFM3 latencia por orden | p50 **{l['latency']['p50']:.2f} s** · p90 {l['latency']['p90']:.2f} s |")
        if l.get("latency_model_only"):
            m.append(f"| LFM3 latencia (solo las que usan el modelo) | p50 {l['latency_model_only']['p50']:.2f} s · "
                     f"prompt {l['prompt_tokens_p50']:.0f} tok, en caché {l['cached_tokens_p50']:.0f} |")
        m.append(f"| LFM3 velocidad | lectura {l['speed']['prompt_tok_s']:.0f} tok/s · "
                 f"generación {l['speed']['gen_tok_s']:.0f} tok/s |")
    if "tts" in r:
        for lang, v in r["tts"].items():
            line = f"| Voz {lang} | frase corta {v['short']['synth_s']:.2f} s (RTF {v['short']['rtf']:.2f})"
            if "long" in v:
                line += f" · primer audio de texto largo {v['long']['first_chunk_s']:.2f} s"
            m.append(line + " |")
    if "vision" in r and "latency" in r["vision"]:
        v = r["vision"]
        m.append(f"| Visión LFM3-VL | carga {v['load_s']:.1f} s · respuesta p50 {v['latency']['p50']:.2f} s · "
                 f"{v['correct']}/{v['total']} correctas |")
    if "e2e_estimate_s" in r:
        m.append(f"| **De soltar el atajo a oír la respuesta (estimado)** | **{r['e2e_estimate_s']:.2f} s** |")
    if r.get("notes"):
        m.append(f"\n_{r['notes']}_")
    return "\n".join(m)


def submit_url(r: dict, md: str) -> str:
    s = r["system"]
    title = f"[Benchmark] {s['gpu'] or 'CPU'} · {s['cpu'][:40]}"
    compact = {k: v for k, v in r.items() if k not in ("stt", "llm")}
    compact["stt"] = {k: v for k, v in r.get("stt", {}).items() if k != "langs"}
    compact["stt"]["langs"] = {l: {k: v for k, v in d.items() if k != "items"} for l, d in r.get("stt", {}).get("langs", {}).items()}
    compact["llm"] = {k: v for k, v in r.get("llm", {}).items() if k != "items"}
    compact.get("vision", {}).pop("answers", None)
    params = {"template": "benchmark.yml", "title": title, "labels": "benchmark",
              "results": md, "json": json.dumps(compact, ensure_ascii=False, separators=(",", ":"))}
    url = f"{REPO}/issues/new?" + urllib.parse.urlencode(params)
    if len(url) > 7800:  # GitHub corta las URL muy largas: el JSON se adjunta a mano
        params["json"] = "(adjunta aquí el archivo .json que guardó el benchmark)"
        url = f"{REPO}/issues/new?" + urllib.parse.urlencode(params)
    return url


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark de Kalmiya")
    ap.add_argument("--quick", action="store_true", help="menos frases y sin visión")
    ap.add_argument("--no-vision", action="store_true")
    ap.add_argument("--port", type=int, default=8771, help="puerto para el llama-server propio")
    ap.add_argument("--out", type=Path, default=Path.home() / "Documents" / "Kalmiya" / "benchmark")
    ap.add_argument("--notes", default="", help="comentario libre (p. ej. 'laptop enchufada')")
    ap.add_argument("--submit", action="store_true", help="abrir GitHub para compartir el resultado")
    ap.add_argument("--ask", action="store_true",
                    help="preguntar al final si compartirlo y esperar Enter (acceso del menú Inicio)")
    a = ap.parse_args()
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    a.out.mkdir(parents=True, exist_ok=True)
    log = lambda msg: print(msg, flush=True)
    cfg = Config.load()
    t0 = time.perf_counter()
    log("Kalmiya benchmark: mide voz a texto, LFM3, voz y visión en este equipo (2-4 min).\n"
        "Las órdenes se simulan: no se abre ni se toca nada.\n\n1/5 equipo")
    r = {"schema": SCHEMA, "date": dt.datetime.now().isoformat(timespec="seconds"), "system": system_info(cfg),
         "notes": a.notes}
    log("2/5 voz (Piper)")
    r["tts"] = bench_tts(cfg, log)
    log("3/5 voz a texto (Whisper)")
    r["stt"] = bench_stt(cfg, a.quick, log)
    log("4/5 LFM3")
    r["llm"] = bench_llm(cfg, a.port, a.out, log)
    if not (a.quick or a.no_vision):
        log("5/5 visión (LFM3-VL)")
        r["vision"] = bench_vision(cfg, log)
    try:
        r["e2e_estimate_s"] = round(r["stt"]["langs"]["es"]["latency"]["p50"] + r["llm"]["latency"]["p50"]
                                    + r["tts"]["es"]["short"]["first_chunk_s"], 2)
    except KeyError:
        pass
    r["duration_s"] = round(time.perf_counter() - t0, 1)
    host = re.sub(r"[^\w-]", "", platform.node())[:20] or "pc"
    stem = f"kalmiya-bench-{host}-{dt.datetime.now():%Y%m%d-%H%M}"
    (a.out / f"{stem}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    md = to_markdown(r)
    (a.out / f"{stem}.md").write_text(md, encoding="utf-8")
    log("\n" + md + f"\n\nGuardado en {a.out / stem}.json (y .md)")
    if a.ask and not a.submit:
        a.submit = input("\n¿Compartir tus resultados en GitHub? Se abrirá el formulario ya rellenado (s/n): "
                         ).strip().lower().startswith(("s", "y"))
    if a.submit:
        webbrowser.open(submit_url(r, md))
        log("Se abrió GitHub con tus resultados: revisa y pulsa «Create».")
    if a.ask:
        input("\nPulsa Enter para cerrar.")


if __name__ == "__main__":
    main()
