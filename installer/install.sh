#!/usr/bin/env bash
# Instalador de Kalmiya para Linux (Wayland + Hyprland; probado en Omarchy / Arch).
#   ./Instalar.sh                     # detecta la GPU (CUDA si hay NVIDIA, si no CPU)
#   ./Instalar.sh --gpu vulkan        # AMD/Intel
#   ./Instalar.sh --quant Q8_0 --no-vision --no-startup --no-launch
# Todo queda en ~/.local/share/kalmiya; los atajos, en ~/.config/hypr/kalmiya.lua.
set -euo pipefail

GPU=auto QUANT=Q4_K_M VISION=1 STARTUP=1 LAUNCH=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --gpu) GPU="$2"; shift ;;
    --quant) QUANT="$2"; shift ;;
    --no-vision) VISION=0 ;;
    --no-startup) STARTUP=0 ;;
    --no-launch) LAUNCH=0 ;;
    -h|--help) sed -n 2,7p "$0"; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; exit 1 ;;
  esac
  shift
done

ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/kalmiya"
APP="$ROOT/app" VENV="$ROOT/venv" BIN="$ROOT/bin/llama" MODELS="$ROOT/models"
HYPR="${XDG_CONFIG_HOME:-$HOME/.config}/hypr"
LOCALBIN="$HOME/.local/bin"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STEP=0 TOTAL=$((VISION ? 10 : 9))

step() { STEP=$((STEP + 1)); printf '\n\033[1;36m==> [%d/%d] %s\033[0m\n' "$STEP" "$TOTAL" "$1"; }
die() { printf '\033[1;31mError: %s\033[0m\n' "$1" >&2; exit 1; }
download() {  # url destino
  if [[ -s "$2" ]]; then echo "   ya existe: $(basename "$2")"; return; fi
  echo "   descargando $(basename "$2")"
  curl -fL --retry 3 -C - --progress-bar -o "$2.part" "$1" || die "falló la descarga de $1"
  mv "$2.part" "$2"
}

printf '\033[1;32mInstalando Kalmiya en %s\033[0m\n' "$ROOT"

# 0. Programas del sistema que usa la versión de Linux
step "Comprobando programas del sistema"
declare -A PKG=([wtype]=wtype [wl-copy]=wl-clipboard [grim]=grim [slurp]=slurp [tesseract]=tesseract
                [pactl]=libpulse [hyprctl]=hyprland [curl]=curl [notify-send]=libnotify)
missing=()
for cmd in "${!PKG[@]}"; do command -v "$cmd" >/dev/null || missing+=("${PKG[$cmd]}"); done
[[ -e /usr/lib/libgtk4-layer-shell.so ]] || missing+=(gtk4-layer-shell)
[[ -e /usr/lib/libportaudio.so ]] || missing+=(portaudio)
ls /usr/lib/girepository-1.0/AyatanaAppIndicator3-0.1.typelib >/dev/null 2>&1 || missing+=(libayatana-appindicator)
if ((${#missing[@]})); then
  echo "   faltan: ${missing[*]}"
  if command -v pacman >/dev/null; then
    sudo pacman -S --needed --noconfirm "${missing[@]}" || die "no se pudieron instalar: ${missing[*]}"
  else
    die "instálalos con el gestor de paquetes de tu distribución y vuelve a ejecutar el instalador"
  fi
fi
command -v hyprctl >/dev/null && hyprctl version >/dev/null 2>&1 || echo "   aviso: Hyprland no está corriendo; los atajos se activarán al iniciar sesión"

mkdir -p "$ROOT" "$APP" "$BIN" "$MODELS/piper" "$MODELS/tessdata" "$LOCALBIN" "$ROOT/logs"

# 1. La aplicación
step "Copiando Kalmiya"
pkill -f "$VENV/bin/python -m kalmiya" 2>/dev/null || true
rm -rf "$APP/kalmiya"
cp -r "$SRC/kalmiya" "$APP/"
find "$APP/kalmiya" -name __pycache__ -prune -exec rm -rf {} +
cp "$SRC/requirements-linux.txt" "$SRC/installer/uninstall.sh" "$APP/"

# 2. Python aislado (uv), sin tocar el del sistema
step "Preparando Python"
UV="$(command -v uv || true)"
if [[ -z "$UV" ]]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV="$HOME/.local/bin/uv"
fi
"$UV" venv --allow-existing -q -p 3.12 "$VENV"
PY="$VENV/bin/python"

if [[ "$GPU" == auto ]]; then
  if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then GPU=cuda; else GPU=cpu; fi
fi
echo "   backend: $GPU"

step "Instalando dependencias"
"$UV" pip install -q -p "$PY" -r "$APP/requirements-linux.txt"
if [[ "$GPU" == cuda ]]; then
  # cuBLAS/cuDNN para Whisper en GPU (~0.2 s por frase en vez de ~4 s en CPU)
  "$UV" pip install -q -p "$PY" nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10"
fi

# 3. llama.cpp (llama-server) según la GPU
step "Descargando el motor de IA (llama.cpp)"
case "$GPU" in
  cuda) PATTERN="bin-ubuntu-cuda-12.8-x64.tar.gz" ;;
  vulkan) PATTERN="bin-ubuntu-vulkan-x64.tar.gz" ;;
  cpu) PATTERN="bin-ubuntu-x64.tar.gz" ;;
  *) die "--gpu debe ser auto, cuda, vulkan o cpu" ;;
esac
RELEASE_JSON="$(curl -fsSL -H "User-Agent: kalmiya-installer" "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10")"
read -r TAG URLS < <("$PY" -I -c '
import json, sys
pattern, gpu = sys.argv[1], sys.argv[2]
for r in json.load(sys.stdin):
    assets = [a for a in r["assets"] if a["name"].startswith("llama-") and a["name"].endswith(pattern)]
    if gpu == "cuda":
        assets += [a for a in r["assets"] if a["name"].startswith("cudart-") and a["name"].endswith(pattern)]
    if assets:
        print(r["tag_name"], ",".join(a["browser_download_url"] for a in assets))
        break
' "$PATTERN" "$GPU" <<<"$RELEASE_JSON")
[[ -n "${TAG:-}" ]] || die "no encontré un build de llama.cpp para $GPU"
if [[ "$(cat "$BIN/VERSION" 2>/dev/null)" != "$TAG-$GPU" ]]; then
  rm -rf "${BIN:?}"/*
  TMP="$(mktemp -d)"
  IFS=, read -ra LIST <<<"$URLS"
  for u in "${LIST[@]}"; do
    download "$u" "$TMP/$(basename "$u")"
    tar -xzf "$TMP/$(basename "$u")" -C "$BIN"
  done
  rm -rf "$TMP"
  echo "$TAG-$GPU" > "$BIN/VERSION"
fi
SERVER="$(find "$BIN" -name llama-server -type f | head -1)"
[[ -n "$SERVER" ]] || die "el paquete de llama.cpp no trae llama-server"
chmod +x "$SERVER"
# Las .so (libggml-cuda, libcudart…) se buscan junto al binario.
find "$BIN" -name '*.so*' -exec cp -n {} "$(dirname "$SERVER")/" \; 2>/dev/null || true

# 4. Modelos
step "Descargando LFM3 (el cerebro)"
GGUF="LFM3-1.7B-$QUANT.gguf"
download "https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF/resolve/main/$GGUF" "$MODELS/$GGUF"

if ((VISION)); then
  step "Descargando LFM3-VL (para ver imágenes)"
  VL="https://huggingface.co/LiquidAI/LFM3-VL-2B-GGUF/resolve/main"
  download "$VL/LFM3-VL-2B-Q4_K_M.gguf" "$MODELS/LFM3-VL-2B-Q4_K_M.gguf"
  download "$VL/mmproj-LFM3-VL-2B-Q8_0.gguf" "$MODELS/mmproj-LFM3-VL-2B-Q8_0.gguf"
fi

step "Descargando las voces y el OCR"
PIPER="https://huggingface.co/rhasspy/piper-voices/resolve/main"
for v in es/es_AR/daniela/high/es_AR-daniela-high es/es_MX/claude/high/es_MX-claude-high \
         en/en_US/lessac/high/en_US-lessac-high; do
  download "$PIPER/$v.onnx" "$MODELS/piper/$(basename "$v").onnx"
  download "$PIPER/$v.onnx.json" "$MODELS/piper/$(basename "$v").onnx.json"
done
# Tesseract en español e inglés (para leer los recomendados de YouTube y la lista de WhatsApp)
for l in spa eng; do
  download "https://github.com/tesseract-ocr/tessdata_fast/raw/main/$l.traineddata" "$MODELS/tessdata/$l.traineddata"
done

# En GPU NVIDIA large-v3-turbo es rápido y preciso; en CPU, "small" mantiene la latencia baja.
WHISPER=$([[ "$GPU" == cuda ]] && echo large-v3-turbo || echo small)
step "Descargando Whisper (voz a texto)"
"$PY" -I -c "from faster_whisper.utils import download_model; download_model('$WHISPER', cache_dir='$MODELS/whisper')" \
  || die "no se pudo descargar Whisper"

# 5. Configuración inicial (sin pisar la del usuario), lanzadores y atajos
step "Creando accesos y atajos"
if [[ ! -f "$ROOT/config.json" ]]; then
  NGL=$([[ "$GPU" == cpu ]] && echo 0 || echo 99)
  printf '{\n  "llm_gguf": "%s",\n  "llm_gpu_capas": %s,\n  "whisper_modelo": "%s"\n}\n' "$GGUF" "$NGL" "$WHISPER" > "$ROOT/config.json"
fi

# Lanzador: hace visibles cuBLAS/cuDNN de las wheels de pip para Whisper (CTranslate2).
cat > "$LOCALBIN/kalmiya" <<EOF
#!/usr/bin/env bash
# Kalmiya, asistente de voz local. Sin argumentos la abre; con uno (settings, calendar, quit…) se lo pide.
NV="\$(ls -d "$VENV"/lib/python3*/site-packages/nvidia 2>/dev/null | head -1)"
if [[ -n "\$NV" ]]; then
  for d in "\$NV"/*/lib; do LD_LIBRARY_PATH="\$d:\${LD_LIBRARY_PATH:-}"; done
  export LD_LIBRARY_PATH
fi
cd "$APP" && exec "$VENV/bin/python" -m kalmiya "\$@"
EOF
# Lo llaman los atajos de Hyprland en cada pulsación: bash puro, sin arrancar Python.
cat > "$LOCALBIN/kalmiya-ctl" <<'EOF'
#!/usr/bin/env bash
# Manda una orden a Kalmiya (press agente, release agente, nueva, imagen, esc, settings…).
exec 3<>/dev/tcp/127.0.0.1/47653 2>/dev/null || exit 1
printf '%s\n' "$*" >&3
EOF
chmod +x "$LOCALBIN/kalmiya" "$LOCALBIN/kalmiya-ctl"

ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
mkdir -p "$ICON_DIR"
(cd "$APP" && "$PY" -c "from kalmiya.ui import make_icon; make_icon(size=256).save('$ICON_DIR/kalmiya.png')") || true

mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/kalmiya.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Kalmiya
GenericName=Asistente de voz
Comment=Asistente de voz local: Ctrl+Alt+Espacio y habla
Exec=$LOCALBIN/kalmiya
Icon=kalmiya
Terminal=false
Categories=Utility;Accessibility;
Actions=Settings;Calendar;Quit;

[Desktop Action Settings]
Name=Configuración
Exec=$LOCALBIN/kalmiya settings

[Desktop Action Calendar]
Name=Calendario
Exec=$LOCALBIN/kalmiya calendar

[Desktop Action Quit]
Name=Salir
Exec=$LOCALBIN/kalmiya quit
EOF
cat > "$HOME/.local/share/applications/kalmiya-uninstall.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Desinstalar Kalmiya
Exec=bash $APP/uninstall.sh
Icon=kalmiya
Terminal=true
Categories=Utility;
EOF

# Hyprland: cargar los atajos que genera Kalmiya (hypr/kalmiya.lua) y arrancarla al iniciar sesión.
if [[ -f "$HYPR/hyprland.lua" ]]; then
  [[ -f "$HYPR/kalmiya.lua" ]] || (cd "$APP" && "$PY" -c "from kalmiya.linux import hotkeys; from kalmiya.config import Config; hotkeys.CONF.write_text(hotkeys.render(Config.load()))")
  if ! grep -q 'hypr.kalmiya' "$HYPR/hyprland.lua"; then
    cp "$HYPR/hyprland.lua" "$HYPR/hyprland.lua.bak.$(date +%s)"
    printf '\n-- Kalmiya (asistente de voz): sus atajos, generados en hypr/kalmiya.lua\npcall(require, "hypr.kalmiya")\n' >> "$HYPR/hyprland.lua"
  fi
  if ((STARTUP)) && [[ -f "$HYPR/autostart.lua" ]] && ! grep -q 'kalmiya' "$HYPR/autostart.lua"; then
    cp "$HYPR/autostart.lua" "$HYPR/autostart.lua.bak.$(date +%s)"
    printf '\n-- Kalmiya (asistente de voz)\no.launch_on_start("%s")\n' "$LOCALBIN/kalmiya" >> "$HYPR/autostart.lua"
  elif ((!STARTUP)) && [[ -f "$HYPR/autostart.lua" ]]; then
    sed -i '/-- Kalmiya (asistente de voz)/d; /kalmiya")$/d' "$HYPR/autostart.lua"
  fi
  if hyprctl version >/dev/null 2>&1; then
    hyprctl reload config-only >/dev/null
    errs="$(hyprctl configerrors 2>/dev/null)"
    [[ -z "$errs" || "$errs" == *"no errors"* ]] || echo "   aviso de Hyprland: $errs"
  fi
elif [[ -f "$HYPR/hyprland.conf" ]]; then
  echo "   Config clásica de Hyprland: añade estos atajos a hyprland.conf:"
  echo "     bind = CTRL ALT, SPACE, exec, kalmiya-ctl press agente"
  echo "     bindr = CTRL ALT, SPACE, exec, kalmiya-ctl release agente"
  echo "     bind = CTRL ALT, D, exec, kalmiya-ctl press dictado"
  echo "     bindr = CTRL ALT, D, exec, kalmiya-ctl release dictado"
  echo "     exec-once = $LOCALBIN/kalmiya"
fi

printf '\n\033[1;32mKalmiya instalada.\033[0m Mantén Ctrl+Alt+Espacio y habla; Ctrl+Alt+D para dictar.\n'
if ((LAUNCH)); then
  if command -v uwsm-app >/dev/null; then uwsm-app -- "$LOCALBIN/kalmiya" >/dev/null 2>&1 &
  else setsid "$LOCALBIN/kalmiya" >/dev/null 2>&1 & fi
fi
