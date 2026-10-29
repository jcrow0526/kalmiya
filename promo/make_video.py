"""Video de presentación de Kalmiya (español e inglés) con narración, música y subtítulos.

Usa el orbe y las voces reales de Kalmiya:
    %LOCALAPPDATA%\\Kalmiya\\venv\\Scripts\\python.exe promo\\make_video.py [es|en]
Genera promo/Kalmiya_<idioma>.mp4 (subtítulos quemados + pista de subtítulos) y su .srt.
"""
import math
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from kalmiya.orb import OrbRenderer  # noqa: E402
from kalmiya.tts import Speaker  # noqa: E402

import imageio_ffmpeg  # noqa: E402

W, H, FPS, SR = 1920, 1080, 30, 44100
ACCENT = (124, 255, 43)
FONTS = r"C:\Windows\Fonts"
VOICE = {"es": "es_AR-daniela-high.onnx", "en": "en_US-lessac-high.onnx"}

# Cada escena: lo que dice (say), el subtítulo si difiere (sub) y lo que se ve.
SCRIPT = {
    "es": [
        dict(kind="intro", say="Hola, soy Kalmiya."),
        dict(say="Corro de manera local, en tu computadora. Tu voz nunca sale de tu PC.",
             eyebrow="PRIVACIDAD", title="100 % local",
             body="Sin nube: escucho, pienso y hablo dentro de tu PC."),
        dict(say="Y estoy aquí para ayudarte.", eyebrow="TU ASISTENTE", title="Estoy para ayudarte",
             body="Escucho, entiendo y actúo en tu computadora."),
        dict(say="Mantén Control, Alt, Espacio, y pídeme lo que necesites.",
             sub="Mantén Ctrl + Alt + Espacio y pídeme lo que necesites.",
             eyebrow="CÓMO USARME", title="Mantén, habla, suelta", keys=["Ctrl", "Alt", "Espacio"]),
        dict(say="Pongo tu música y tus videos, abro tus aplicaciones y cierro pestañas.",
             eyebrow="CONTROLA TU PC", title="Música, apps y pestañas",
             chips=["«Pon Bad Bunny»", "«Abre el primer recomendado»", "«Cierra la pestaña de YouTube»"]),
        dict(say="Agendo tus citas, te recuerdo pendientes y te resumo las noticias.",
             eyebrow="TU DÍA", title="Agenda, recordatorios y noticias",
             chips=["«Agéndame dentista el viernes a las 4»", "«Recuérdame sacar la ropa en 20 minutos»",
                    "«Resúmeme las noticias»"]),
        dict(say="Escribo tus mensajes de WhatsApp, y siempre te pregunto antes de enviarlos.",
             eyebrow="MENSAJES", title="WhatsApp con tu voz",
             chips=["«Dile a Ana que ya voy»"], reply="Ya voy. ¿Lo envío?"),
        dict(say="Muéstrame una imagen, y te digo qué hay en ella.", eyebrow="VISIÓN",
             title="Pregúntame por una imagen", keys=["Ctrl", "Alt", "I"], chips=["«¿Qué es esto?»"]),
        dict(kind="outro", say="Kalmiya. Tu asistente de voz, cien por ciento local.",
             sub="Kalmiya. Tu asistente de voz, 100 % local.", tagline="Tu asistente de voz · 100 % local",
             hint="Mantén Ctrl + Alt + Espacio y habla"),
    ],
    "en": [
        dict(kind="intro", say="Hi, I'm Kalmiya."),
        dict(say="I run locally, right on your computer. Your voice never leaves your PC.",
             eyebrow="PRIVACY", title="100% local",
             body="No cloud: I listen, think and speak inside your PC."),
        dict(say="And I'm here to help you.", eyebrow="YOUR ASSISTANT", title="Here to help you",
             body="I listen, understand and act on your computer."),
        dict(say="Hold Control, Alt, Space, and ask me for anything.",
             sub="Hold Ctrl + Alt + Space and ask me for anything.",
             eyebrow="HOW TO USE ME", title="Hold, talk, release", keys=["Ctrl", "Alt", "Space"]),
        dict(say="I play your music and videos, open your apps, and close tabs.",
             eyebrow="CONTROL YOUR PC", title="Music, apps and tabs",
             chips=["“Play Bad Bunny”", "“Open the first recommended video”", "“Close the YouTube tab”"]),
        dict(say="I schedule appointments, remind you of things, and summarize the news.",
             eyebrow="YOUR DAY", title="Calendar, reminders and news",
             chips=["“Add dentist on Friday at 4”", "“Remind me to do the laundry in 20 minutes”",
                    "“Summarize the news”"]),
        dict(say="I write your WhatsApp messages, and always ask before sending them.",
             eyebrow="MESSAGES", title="WhatsApp with your voice",
             chips=["“Tell Ana I'm on my way”"], reply="On my way. Send it?"),
        dict(say="Show me an image, and I'll tell you what's in it.", eyebrow="VISION",
             title="Ask me about an image", keys=["Ctrl", "Alt", "I"], chips=["“What is this?”"]),
        dict(kind="outro", say="Kalmiya. Your voice assistant, one hundred percent local.",
             sub="Kalmiya. Your voice assistant, 100% local.", tagline="Your voice assistant · 100% local",
             hint="Hold Ctrl + Alt + Space and talk"),
    ],
}


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(f"{FONTS}\\{name}", size)


F_HUGE, F_TITLE = font("segoeuib.ttf", 150), font("segoeuib.ttf", 74)
F_EYEBROW, F_BODY = font("seguisb.ttf", 28), font("segoeui.ttf", 34)
F_CHIP, F_KEY, F_SUB = font("segoeui.ttf", 32), font("seguisb.ttf", 40), font("seguisb.ttf", 36)
F_TAG = font("segoeuil.ttf", 46)


def smooth(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


# ------------------------------------------------------------------ audio

def narration(lang: str, scenes: list[dict]) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """Voz de cada escena colocada en la línea de tiempo; devuelve audio y (inicio, fin) de cada frase."""
    sp = Speaker(VOICE[lang], speed=0.95)
    clips = []
    for s in scenes:
        a, sr = sp._piper_audio(s["say"])
        a = np.interp(np.arange(0, len(a), sr / SR), np.arange(len(a)), a).astype(np.float32)
        clips.append(a / max(1e-6, np.abs(a).max()) * 0.85)
    t, spans, parts = 0.0, [], []
    for i, a in enumerate(clips):
        lead = 1.1 if i == 0 else 0.55
        tail = 1.6 if i == len(clips) - 1 else 0.75
        start = t + lead
        spans.append((start, start + len(a) / SR))
        t = start + len(a) / SR + tail
        scenes[i]["t0"], scenes[i]["t1"] = (spans[-1][0] - lead), t
    out = np.zeros(int(t * SR) + SR, np.float32)
    for (s, _), a in zip(spans, clips):
        i = int(s * SR)
        out[i:i + len(a)] += a
    return out[:int(t * SR)], spans


def music(n: int, voice: np.ndarray) -> np.ndarray:
    """Colchón suave (Am - F - C - G) que baja cuando habla Kalmiya."""
    t = np.arange(n) / SR
    chords = [[220.0, 261.63, 329.63], [174.61, 220.0, 261.63], [261.63, 329.63, 392.0], [196.0, 246.94, 293.66]]
    bar = 4.0
    out = np.zeros(n, np.float32)
    for k in range(int(n / SR / bar) + 1):
        f = chords[k % 4]
        s, e = int(k * bar * SR), min(n, int((k + 1) * bar * SR + 1.5 * SR))
        if s >= n:
            break
        tt = t[s:e] - k * bar
        env = np.minimum(1, tt / 1.2) * np.exp(-np.maximum(0, tt - bar) / 0.6)
        tone = sum(np.sin(2 * np.pi * fr * tt) + 0.35 * np.sin(2 * np.pi * fr * 2.003 * tt)
                   + 0.5 * np.sin(2 * np.pi * fr / 2 * tt) for fr in f)
        out[s:e] += (tone * env * 0.05).astype(np.float32)
    # brillo: arpegio suave en octava alta
    for k in range(int(n / SR / 0.5)):
        s = int(k * 0.5 * SR)
        f = chords[int(k * 0.5 / bar) % 4][k % 3] * 2
        tt = np.arange(min(int(1.2 * SR), n - s)) / SR
        out[s:s + len(tt)] += (np.sin(2 * np.pi * f * tt) * np.exp(-tt / 0.35) * 0.018).astype(np.float32)
    # ducking bajo la voz
    env = np.convolve(np.abs(voice), np.ones(4410) / 4410, mode="same")
    duck = 1 - 0.6 * np.clip(env / (env.max() + 1e-9) * 3, 0, 1)
    fade = np.minimum(1, t / 2.0) * np.minimum(1, (t[-1] - t) / 2.5)
    return out * duck * fade


def write_wav(path: Path, audio: np.ndarray) -> None:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())


def srt_time(s: float) -> str:
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


# ------------------------------------------------------------------ imagen

def background() -> np.ndarray:
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.hypot((x - W * 0.45) / W, (y - H * 0.5) / H)
    base = np.array([10, 11, 15], np.float32) + np.array([8, 9, 14], np.float32) * np.exp(-r * r * 6)[..., None]
    vign = 1 - 0.35 * np.clip(r * 1.3, 0, 1) ** 2
    return base * vign[..., None]


def composite_orb(frame: np.ndarray, orb: np.ndarray, cx: float, cy: float, scale: float) -> None:
    """Pega el orbe (BGRA premultiplicado) centrado en (cx, cy)."""
    img = Image.fromarray(orb[..., [2, 1, 0, 3]], "RGBA")
    if abs(scale - 1) > 1e-3:
        size = max(8, int(orb.shape[0] * scale))
        img = img.resize((size, size), Image.BILINEAR)
    o = np.asarray(img, np.float32)
    s = o.shape[0]
    x0, y0 = int(cx - s / 2), int(cy - s / 2)
    fx0, fy0, fx1, fy1 = max(0, x0), max(0, y0), min(W, x0 + s), min(H, y0 + s)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    sub = o[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0]
    a = sub[..., 3:4] / 255
    region = frame[fy0:fy1, fx0:fx1]
    frame[fy0:fy1, fx0:fx1] = region * (1 - a) + sub[..., :3]


def rgba(c, a: float):
    return (*c, int(max(0, min(1, a)) * 255))


def chip(d: ImageDraw.ImageDraw, x: int, y: int, text: str, a: float, accent: bool = False) -> int:
    w = int(F_CHIP.getlength(text)) + 56
    h = 66
    if accent:
        d.rounded_rectangle((x, y, x + w, y + h), 33, fill=rgba(ACCENT, a))
        d.text((x + 28, y + 13), text, font=F_CHIP, fill=rgba((12, 14, 10), a))
    else:
        d.rounded_rectangle((x, y, x + w, y + h), 33, fill=rgba((255, 255, 255), 0.075 * a),
                            outline=rgba((255, 255, 255), 0.22 * a), width=2)
        d.text((x + 28, y + 13), text, font=F_CHIP, fill=rgba((236, 238, 244), a))
    return h


def keycap(d: ImageDraw.ImageDraw, x: int, y: int, text: str, a: float) -> int:
    w = max(96, int(F_KEY.getlength(text)) + 52)
    d.rounded_rectangle((x, y + 8, x + w, y + 96), 18, fill=rgba((0, 0, 0), 0.5 * a))
    d.rounded_rectangle((x, y, x + w, y + 88), 18, fill=rgba((34, 36, 44), a),
                        outline=rgba((255, 255, 255), 0.2 * a), width=2)
    d.text((x + w / 2, y + 44), text, font=F_KEY, fill=rgba((244, 245, 248), a), anchor="mm")
    return w


def wrap(text: str, f, max_w: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        test = f"{line} {word}".strip()
        if f.getlength(test) <= max_w or not line:
            line = test
        else:
            lines.append(line)
            line = word
    return lines + [line]


def draw_scene(d: ImageDraw.ImageDraw, s: dict, t: float) -> None:
    local, dur = t - s["t0"], s["t1"] - s["t0"]
    out = 1 - smooth((local - (dur - 0.45)) / 0.4)  # salida

    def fade(delay: float) -> float:
        return smooth((local - delay) / 0.55) * out

    kind = s.get("kind", "feature")
    if kind in ("intro", "outro"):
        a = fade(0.5 if kind == "intro" else 0.3)
        y = 720 + (1 - a) * 30
        d.text((W / 2, y), "Kalmiya", font=F_HUGE, fill=rgba((246, 247, 250), a), anchor="mm")
        if kind == "outro":
            a2 = fade(0.9)
            d.text((W / 2, y + 112), s["tagline"], font=F_TAG, fill=rgba((190, 194, 204), a2), anchor="mm")
            a3 = fade(1.4)
            d.text((W / 2, 110), s["hint"], font=F_EYEBROW, fill=rgba(ACCENT, a3), anchor="mm")
        return
    x = 1010
    y = 250
    a = fade(0.15)
    dy = (1 - a) * 26
    d.text((x, y + dy), s["eyebrow"], font=F_EYEBROW, fill=rgba(ACCENT, a))
    y += 46
    a = fade(0.3)
    for line in wrap(s["title"], F_TITLE, 820):
        d.text((x, y + (1 - a) * 26), line, font=F_TITLE, fill=rgba((246, 247, 250), a))
        y += 92
    y += 18
    if s.get("body"):
        a = fade(0.55)
        for line in wrap(s["body"], F_BODY, 800):
            d.text((x, y + (1 - a) * 20), line, font=F_BODY, fill=rgba((176, 180, 192), a))
            y += 50
        y += 20
    if s.get("keys"):
        kx = x
        for i, k in enumerate(s["keys"]):
            a = fade(0.6 + 0.18 * i)
            kx += keycap(d, kx, int(y + (1 - a) * 20), k, a) + 16
            if i < len(s["keys"]) - 1:
                d.text((kx, y + 44), "+", font=F_KEY, fill=rgba((130, 134, 146), a), anchor="lm")
                kx += 40
        y += 124
    for i, c in enumerate(s.get("chips", [])):
        a = fade(0.75 + 0.35 * i)
        y += chip(d, x, int(y + (1 - a) * 20), c, a) + 18
    if s.get("reply"):
        a = fade(0.75 + 0.35 * len(s.get("chips", [])) + 0.5)
        # La respuesta de Kalmiya va alineada a la derecha, como en un chat.
        w = int(F_CHIP.getlength(s["reply"])) + 56
        chip(d, x + 760 - w, int(y + (1 - a) * 20), s["reply"], a, accent=True)


def draw_subtitle(d: ImageDraw.ImageDraw, text: str, a: float) -> None:
    lines = wrap(text, F_SUB, 1400)
    lh = 50
    box_h = lh * len(lines) + 34
    y0 = H - 70 - box_h
    w = max(F_SUB.getlength(l) for l in lines) + 60
    d.rounded_rectangle((W / 2 - w / 2, y0, W / 2 + w / 2, y0 + box_h), 18, fill=rgba((0, 0, 0), 0.62 * a))
    for i, l in enumerate(lines):
        d.text((W / 2, y0 + 17 + lh * i + lh / 2), l, font=F_SUB, fill=rgba((255, 255, 255), a), anchor="mm")


def orb_place(scenes: list[dict], t: float) -> tuple[float, float, float]:
    """Centro y escala del orbe: al centro en intro/outro, a la izquierda en las demás."""
    def target(s):
        # Sin escalar: PIL redimensiona como alfa no premultiplicado y deja un halo oscuro.
        return (W / 2, 380, 1.0) if s.get("kind") in ("intro", "outro") else (520, 500, 1.0)
    i = next((k for k, s in enumerate(scenes) if s["t0"] <= t < s["t1"]), len(scenes) - 1)
    cur = target(scenes[i])
    if i == 0:
        return cur
    prev = target(scenes[i - 1])
    k = smooth((t - scenes[i]["t0"]) / 0.9)
    return tuple(p + (c - p) * k for p, c in zip(prev, cur))


# ------------------------------------------------------------------ render

def render(lang: str) -> Path:
    scenes = [dict(s) for s in SCRIPT[lang]]
    print(f"[{lang}] narración…")
    voice, spans = narration(lang, scenes)
    total = len(voice) / SR
    audio = voice + music(len(voice), voice)
    wav = ROOT / f"_audio_{lang}.wav"
    write_wav(wav, audio / max(1.0, np.abs(audio).max() / 0.95))

    srt = ROOT / f"Kalmiya_{lang.upper()}.srt"
    srt.write_text("\n".join(f"{i + 1}\n{srt_time(a)} --> {srt_time(b + 0.25)}\n{s.get('sub', s['say'])}\n"
                             for i, (s, (a, b)) in enumerate(zip(scenes, spans))), encoding="utf-8")

    # Nivel de la voz por fotograma: el orbe "habla".
    hop = SR // FPS
    nf = int(total * FPS)
    lvl = np.array([np.sqrt(np.mean(voice[i * hop:(i + 1) * hop] ** 2) + 1e-12) for i in range(nf)])
    lvl = np.clip(lvl / (np.percentile(lvl[lvl > 1e-3], 95) + 1e-9), 0, 1)

    out = ROOT / f"Kalmiya_{lang.upper()}.mp4"
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-i", str(wav), "-i", str(srt), "-map", "0:v", "-map", "1:a", "-map", "2:s",
           # aq-mode=3 reparte mejor los bits en zonas oscuras (el degradado del fondo hacía bandas).
           "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-x264-params", "aq-mode=3", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-c:s", "mov_text",
           # Sin -shortest: recortaba el video al final del último subtítulo y se perdía el cierre.
           "-metadata:s:s:0", f"language={'spa' if lang == 'es' else 'eng'}", "-movflags", "+faststart",
           str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    renderer = OrbRenderer(560)
    bg = background()
    # Grano muy leve que cambia en cada fotograma: rompe las bandas del degradado oscuro.
    rng = np.random.default_rng(7)
    grain = [rng.normal(0, 1.2, (H, W, 1)).astype(np.float32) for _ in range(6)]
    smooth_lvl = 0.0
    print(f"[{lang}] {nf} fotogramas ({total:.1f} s)…")
    for f in range(nf):
        t = f / FPS
        smooth_lvl += (lvl[f] - smooth_lvl) * 0.5
        speaking = any(a <= t <= b for a, b in spans)
        energy = 1.6 if speaking else 1.0
        orb = renderer.frame(t * 0.9, 0.15 + 0.6 * smooth_lvl, energy)
        cx, cy, sc = orb_place(scenes, t)
        cy += math.sin(t * 1.3) * 6  # flota
        appear = smooth(t / 1.0) * (1 - smooth((t - (total - 0.8)) / 0.8))
        frame = bg + grain[f % len(grain)]
        composite_orb(frame, (orb.astype(np.float32) * appear).astype(np.uint8), cx, cy, sc)
        img = Image.fromarray(np.clip(frame, 0, 255).astype(np.uint8), "RGB")
        d = ImageDraw.Draw(img, "RGBA")
        scene = next((s for s in scenes if s["t0"] <= t < s["t1"]), scenes[-1])
        draw_scene(d, scene, t)
        for s, (a, b) in zip(scenes, spans):
            if a - 0.15 <= t <= b + 0.35:
                draw_subtitle(d, s.get("sub", s["say"]), smooth((t - a + 0.15) / 0.2) * (1 - smooth((t - b - 0.15) / 0.2)))
        proc.stdin.write(img.tobytes())
        if f % 150 == 0:
            print(f"   {f}/{nf}")
    proc.stdin.close()
    proc.wait()
    wav.unlink(missing_ok=True)
    if proc.returncode:
        raise SystemExit(f"ffmpeg falló ({proc.returncode})")
    print(f"[{lang}] listo: {out}")
    return out


if __name__ == "__main__":
    for lang in sys.argv[1:] or ["es", "en"]:
        render(lang)
