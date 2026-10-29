# Desinstala Kalmiya: cierra procesos, borra %LOCALAPPDATA%\Kalmiya y los accesos directos.
# Los videos descargados (Videos\Kalmiya) y eventos (Documentos\Kalmiya) se conservan.
$Root = Join-Path $env:LOCALAPPDATA "Kalmiya"
Get-Process pythonw, python, llama-server -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$Root*" } | Stop-Process -Force
Start-Sleep -Seconds 1
foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Startup"))) {
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $dir "Kalmiya.lnk"), (Join-Path $dir "Desinstalar Kalmiya.lnk")
}
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $Root
Write-Host "Kalmiya desinstalada."
