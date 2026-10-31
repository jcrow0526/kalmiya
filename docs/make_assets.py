"""Recursos de la landing (docs/assets): bucle del orbe, videos web, portadas e icono.

    %LOCALAPPDATA%\\Kalmiya\\venv\\Scripts\\python.exe docs\\make_assets.py [carpeta-con-comerciales-omarchy]
"""
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from kalmiya.orb import OrbRenderer  # noqa: E402
from kalmiya.ui import make_icon  # noqa: E402

FF = imageio_ffmpeg.get_ffmpeg_exe()
OUT = ROOT / "assets"
OUT.mkdir(exist_ok=True)


def orb_loop(size: int = 720, seconds: float = 4.0, fps: int = 30) -> None:
    """Bucle sin cortes del orbe sobre negro puro (el fondo de la página): ida y vuelta."""
    r = OrbRenderer(size)
    frames = []
    for i in range(int(seconds * fps)):
        t = i / fps
        f = r.frame(t * 0.8, 0.22 + 0.12 * np.sin(t * 2.1), 1.3).astype(np.float32)
        frames.append(np.clip(f[..., [2, 1, 0]], 0, 255).astype(np.uint8))  # premultiplicado sobre negro
    frames = frames + frames[-2:0:-1]
    cmd = [FF, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size}x{size}",
           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", "-an", str(OUT / "orb.mp4")]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    p.wait()
    Image.fromarray(frames[0]).save(OUT / "orb.jpg", quality=88)


def web_video(lang: str) -> None:
    src = ROOT.parent / "promo" / f"Kalmiya_{lang}.mp4"
    subprocess.run([FF, "-y", "-loglevel", "error", "-i", str(src), "-map", "0:v", "-map", "0:a",
                    "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "24",
                    "-x264-params", "aq-mode=3", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                    str(OUT / f"kalmiya_{lang.lower()}.mp4")], check=True)
    subprocess.run([FF, "-y", "-loglevel", "error", "-ss", "1.6", "-i", str(src), "-frames:v", "1",
                    "-vf", "scale=1280:-2", str(OUT / f"poster_{lang.lower()}.jpg")], check=True)


def omarchy_video(src_dir: Path, lang: str) -> None:
    """Comerciales de Omarchy (Kalmiya_Omarchy_ES/EN.mp4 del release v0.2.0) a 720p para la web,
    con portada en el momento de "Cambia el tema a Kanagawa"."""
    src = src_dir / f"Kalmiya_Omarchy_{lang}.mp4"
    out = OUT / f"omarchy_{lang.lower()}.mp4"
    subprocess.run([FF, "-y", "-loglevel", "error", "-i", str(src), "-vf", "scale=1280:-2", "-c:v", "libx264",
                    "-preset", "slow", "-crf", "27", "-x264-params", "aq-mode=3", "-c:a", "aac", "-b:a", "112k",
                    "-movflags", "+faststart", str(out)], check=True)
    subprocess.run([FF, "-y", "-loglevel", "error", "-ss", "13", "-i", str(out), "-frames:v", "1", "-q:v", "3",
                    str(OUT / f"omarchy_poster_{lang.lower()}.jpg")], check=True)


if __name__ == "__main__":
    # Opcional: carpeta con los comerciales de Omarchy descargados del release
    # (gh release download v0.2.0 -p "Kalmiya_Omarchy_*.mp4" -D <carpeta>).
    omarchy_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    orb_loop()
    for lang in ("ES", "EN"):
        web_video(lang)
        if omarchy_dir:
            omarchy_video(omarchy_dir, lang)
    make_icon(size=256).save(OUT / "icon.png")
    make_icon(size=64).save(OUT / "favicon.png")
    for p in sorted(OUT.iterdir()):
        print(f"{p.name:22} {p.stat().st_size / 1e6:.1f} MB")
