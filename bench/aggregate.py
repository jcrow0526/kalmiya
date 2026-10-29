"""Genera bench/RESULTS.md a partir de los JSON de bench/results/.

    python bench/aggregate.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def row(r: dict) -> str:
    s, stt, l = r["system"], r.get("stt", {}), r.get("llm", {})
    es = stt.get("langs", {}).get("es", {})
    tts = r.get("tts", {}).get("es", {})
    vis = r.get("vision", {})
    gpu = f"{s.get('gpu') or 'CPU'}" + (f" ({s['vram_gb']} GB)" if s.get("vram_gb") else "")
    cells = [
        gpu, s.get("cpu", "?").split(" w/")[0].strip(), f"{s.get('ram_gb', '?')} GB",
        f"{es['latency']['p50']:.2f} s" if es else "-",
        f"{es['wer_mean']:.0%}" if es else "-",
        f"{l['latency']['p50']:.2f} s" if l else "-",
        f"{l['correct']}/{l['total']}" if l else "-",
        f"{l['speed']['gen_tok_s']:.0f}" if l else "-",
        f"{tts['short']['first_chunk_s']:.2f} s" if tts else "-",
        f"{vis['latency']['p50']:.2f} s" if "latency" in vis else "-",
        f"**{r['e2e_estimate_s']:.2f} s**" if "e2e_estimate_s" in r else "-",
        r.get("date", "")[:10],
    ]
    return "| " + " | ".join(cells) + " |"


def main() -> None:
    results = []
    for f in sorted((ROOT / "results").glob("*.json")):
        try:
            results.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"omito {f.name}: {e}")
    results.sort(key=lambda r: r.get("e2e_estimate_s", 99))
    lines = [
        "# Resultados del benchmark · Benchmark results",
        "",
        "Generado por `python bench/aggregate.py` a partir de [`results/`](results/). Para añadir el tuyo, mira",
        "[README](README.md). Ordenado por tiempo total estimado (de soltar el atajo a oír la respuesta).",
        "",
        "| GPU | CPU | RAM | Voz a texto | Error (WER) | LFM3 por orden | Herramienta correcta | LFM3 tok/s "
        "| Primer audio | Visión | **Total** | Fecha |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
        *[row(r) for r in results],
        "",
    ]
    (ROOT / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"RESULTS.md con {len(results)} resultados")


if __name__ == "__main__":
    main()
