#!/usr/bin/env bash
# Desinstala Kalmiya: cierra la app, borra ~/.local/share/kalmiya, los lanzadores y sus atajos de Hyprland.
# Los videos descargados (Videos/Kalmiya) y los eventos (Documentos/Kalmiya) se conservan.
ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/kalmiya"
HYPR="${XDG_CONFIG_HOME:-$HOME/.config}/hypr"
"$HOME/.local/bin/kalmiya-ctl" quit 2>/dev/null; sleep 1
pkill -f "$ROOT/venv/bin/python -m kalmiya" 2>/dev/null
pkill -f "$ROOT/bin/llama" 2>/dev/null
rm -f "$HOME/.local/bin/kalmiya" "$HOME/.local/bin/kalmiya-ctl" \
      "$HOME/.local/share/applications/kalmiya.desktop" "$HOME/.local/share/applications/kalmiya-uninstall.desktop" \
      "$HOME/.local/share/icons/hicolor/256x256/apps/kalmiya.png" "$HYPR/kalmiya.lua"
[[ -f "$HYPR/hyprland.lua" ]] && sed -i '/-- Kalmiya (asistente de voz)/d; /pcall(require, "hypr.kalmiya")/d' "$HYPR/hyprland.lua"
[[ -f "$HYPR/autostart.lua" ]] && sed -i '/-- Kalmiya (asistente de voz)/d; /kalmiya")$/d' "$HYPR/autostart.lua"
hyprctl reload config-only >/dev/null 2>&1
rm -rf "$ROOT"
echo "Kalmiya desinstalada."
