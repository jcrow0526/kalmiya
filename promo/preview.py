"""Fotogramas sueltos para revisar el diseño antes de renderizar todo el video."""
import sys

import numpy as np
from PIL import Image, ImageDraw

import make_video as mv

lang = sys.argv[1] if len(sys.argv) > 1 else "es"
scenes = [dict(s) for s in mv.SCRIPT[lang]]
voice, spans = mv.narration(lang, scenes)
r, bg = mv.OrbRenderer(560), mv.background()
for i in (0, 3, 6, 8):
    s = scenes[i]
    t = min(spans[i][1] - 0.2, s["t1"] - 0.6)
    frame = bg.copy()
    cx, cy, sc = mv.orb_place(scenes, t)
    mv.composite_orb(frame, r.frame(t, 0.5, 1.6), cx, cy, sc)
    img = Image.fromarray(np.clip(frame, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img, "RGBA")
    mv.draw_scene(d, s, t)
    mv.draw_subtitle(d, s.get("sub", s["say"]), 1.0)
    img.resize((960, 540)).save(mv.ROOT / f"_prev_{lang}_{i}.png")
print("duración", round(len(voice) / mv.SR, 1), "s; escenas", [(round(s["t0"], 1), round(s["t1"], 1)) for s in scenes])
