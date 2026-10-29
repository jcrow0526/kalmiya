# Desinstala Kalmiya: cierra procesos, borra %LOCALAPPDATA%\Kalmiya, los accesos directos y su registro en
# "Aplicaciones instaladas". Guarda una copia del calendario en Documentos\Kalmiya. Los videos descargados
# (Videos\Kalmiya) se conservan. Se abre desde Configuracion de Windows o desde el menu Inicio.
# -Quiet: sin ventanas (desinstalacion silenciosa). Solo ASCII: compatible con Windows PowerShell 5.1.
param([switch]$Quiet)
Add-Type -AssemblyName PresentationFramework

$Root = Join-Path $env:LOCALAPPDATA "Kalmiya"
if (-not $Quiet) {
    $r = [Windows.MessageBox]::Show("Se quitara Kalmiya de esta PC, junto con sus modelos de IA (unos 8 GB)." +
        "`n`nTu calendario se guardara en Documentos\Kalmiya.`n`nContinuar?", "Desinstalar Kalmiya", "YesNo", "Question")
    if ($r -ne "Yes") { exit 0 }
}

Get-Process pythonw, python, llama-server -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "$Root*" } | Stop-Process -Force
Start-Sleep -Seconds 1

# Respaldo del calendario y la configuracion (lo unico personal que hay ahi).
$backup = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Kalmiya"
foreach ($f in @("calendario.json", "config.json")) {
    if (Test-Path (Join-Path $Root $f)) {
        New-Item -ItemType Directory -Force $backup | Out-Null
        Copy-Item -Force (Join-Path $Root $f) (Join-Path $backup "respaldo-$f")
    }
}

foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Startup"))) {
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $dir "Kalmiya.lnk"), (Join-Path $dir "Desinstalar Kalmiya.lnk")
}
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Kalmiya"
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $env:LOCALAPPDATA "Programs\KalmiyaSetup")
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $Root

if (-not $Quiet) {
    $msg = if (Test-Path $Root) { "Kalmiya se desinstalo, pero algunos archivos estaban en uso y quedaron en:`n$Root" }
           else { "Kalmiya se desinstalo." }
    [Windows.MessageBox]::Show($msg, "Kalmiya", "OK", "Information") | Out-Null
}
