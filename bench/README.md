# Benchmark de Kalmiya

Mide cómo funciona Kalmiya **en tu equipo**: cuánto tarda en entenderte, en decidir qué hacer y en
contestarte, y si elige bien. Tarda de 2 a 4 minutos. Las órdenes se simulan: no abre, cierra ni envía nada.

**Resultados de la comunidad:** [RESULTS.md](RESULTS.md)

## Cómo ejecutarlo

- **Con Kalmiya instalada:** menú Inicio → **Kalmiya Benchmark**, o clic derecho en el icono de Kalmiya →
  *Medir este equipo (benchmark)*. Al terminar te pregunta si quieres compartir el resultado.
- **Desde el código:** `python -m kalmiya.bench` (opciones: `--quick`, `--no-vision`, `--notes "…"`, `--submit`).

Guarda dos archivos en `Documentos\Kalmiya\benchmark\`: `kalmiya-bench-<equipo>-<fecha>.json` (todo el
detalle) y `.md` (la tabla resumen).

## Cómo compartir tu resultado

1. **Formulario (lo más fácil):** contesta «s» cuando el benchmark pregunte, o usa `--submit`. Se abre un
   [issue de GitHub](https://github.com/jcrow0526/kalmiya/issues/new?template=benchmark.yml) ya rellenado con
   tu tabla y el JSON; revisa y pulsa *Create*.
2. **Pull request:** copia tu `.json` a `bench/results/` con un nombre como
   `rtx3060_ryzen5-5600_2026-11-02.json`, ejecuta `python bench/aggregate.py` y abre un PR.

El JSON no incluye datos personales: solo modelo de CPU/GPU, RAM, sistema operativo, versiones y tiempos.
El nombre del equipo solo se usa en el nombre del archivo; cámbialo si quieres.

## Qué mide

| Etapa | Cómo | Métricas |
|---|---|---|
| **Equipo** | `nvidia-smi`, registro de Windows | CPU, núcleos, RAM, GPU, VRAM, driver, versión de llama.cpp |
| **Voz a texto** (Whisper) | 8 frases en español y 4 en inglés, generadas con las voces de Kalmiya para que todos midan el mismo audio | latencia p50/p90 por frase y error por palabra (WER; «veinte» = «20») |
| **LFM3** | 24 órdenes reales («pon Bad Bunny», «¿qué tengo mañana?»…) con la herramienta que debe usar | aciertos, latencia por orden, tokens leídos y en caché, velocidad de lectura y generación |
| **Voz** (Piper) | una frase corta y un texto largo | tiempo hasta el primer audio, tiempo total, RTF (tiempo de cálculo ÷ duración del audio) |
| **Visión** (LFM3-VL) | una imagen generada con texto y un círculo de color | carga, latencia, 2 respuestas comprobables |
| **Total estimado** | voz a texto + LFM3 + primer audio | de soltar el atajo a oír la respuesta |

Si Kalmiya está abierta, el benchmark usa su mismo servidor de LFM3 (así mide lo que usas de verdad y no
carga el modelo dos veces). Las cifras son medianas tras calentar los modelos; la primera orden después de
encender la PC tarda unos segundos más mientras se cargan.

**Limitaciones:** las voces sintéticas no son tu voz (con la voz real el error puede ser mayor o menor);
el «total» usa una frase de respuesta con coma, el caso más rápido para la voz. En respuestas sin coma el
primer audio tarda ~0.3–0.45 s más.

## Formato del JSON (`schema: 1`)

```jsonc
{
  "schema": 1, "date": "2026-10-29T12:50:01", "notes": "…", "duration_s": 95.2, "e2e_estimate_s": 0.56,
  "system": {"os", "cpu", "cores", "threads", "ram_gb", "gpu", "vram_gb", "gpu_driver", "llama_cpp",
             "kalmiya", "llm_model", "whisper_model", "voice"},
  "tts":    {"es": {"load_s", "short": {"synth_s", "first_chunk_s", "audio_s", "rtf"}, "long": {…}}, "en": {…}},
  "stt":    {"model", "device", "load_s", "langs": {"es": {"latency": {"p50", "p90", "mean", "n"}, "wer_mean",
             "items": [{"ref", "hyp", "s", "wer", "audio_s"}]}, "en": {…}}},
  "llm":    {"shared_server", "load_s", "accuracy", "correct", "total", "resolved_by_rules", "cache_misses",
             "latency": {…}, "latency_model_only": {…}, "prompt_tokens_p50", "cached_tokens_p50",
             "speed": {"prompt_tok_s", "gen_tok_s", "prompt_n", "predicted_n"},
             "items": [{"text", "expected", "called", "ok", "s", "llm_calls", "prompt_n", "cache_n", …}]},
  "vision": {"load_s", "latency": {…}, "correct", "total", "answers"}
}
```

## Optimizaciones que salieron de este benchmark

Medido en RTX 4070 Laptop + Ryzen 7 7840HS (el primer resultado de la tabla):

| | Antes | Después |
|---|---|---|
| Primer audio de una respuesta corta con coma | 0.43 s | **0.09 s** |
| Primer audio de una respuesta larga (noticias) | 0.67 s (y 1.76 s sin streaming) | **0.15 s** |
| Primera respuesta tras encender | +3.5 s (cargaba la voz al hablar) | se carga al arrancar |
| Total estimado | 0.92 s | **0.56 s** |

- **Voz por fragmentos:** antes se generaba todo el audio y luego se reproducía; ahora suena la primera
  frase mientras se generan las siguientes, y la primera oración se parte en su primera coma.
- **Voz precargada** junto con LFM3 y Whisper al arrancar.
- Se probó y **no** se cambió (sin mejora medible): Whisper sin filtro de voz, sin prompt, con más margen o
  con `beam_size=1` (más error o igual); llama-server con `--parallel 1` (misma latencia, 0 fallos de caché
  en ambos casos).
