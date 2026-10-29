@echo off
rem Abre la ventana del instalador de Kalmiya (sin consola).
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0installer\setup.ps1"
