# Instalador de Kalmiya (asistente de voz con LFM3). Solo ASCII: compatible con Windows PowerShell 5.1.
# Normalmente lo lanza la ventana de setup.ps1 (Instalar.bat); tambien funciona solo en consola:
#   powershell -ExecutionPolicy Bypass -File install.ps1
# Las lineas "@@..." son para la ventana del instalador (progreso), no para personas.
param(
    [ValidateSet("auto", "cpu", "cuda", "vulkan")] [string]$Gpu = "auto",
    [string]$Quant = "Q4_K_M",
    [switch]$NoStartup,
    [switch]$NoVision,
    [switch]$Nemotron,
    [switch]$NoLaunch
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Version = "0.1.1"
$Root   = Join-Path $env:LOCALAPPDATA "Kalmiya"
$Old    = Join-Path $env:LOCALAPPDATA "Eco"     # nombre anterior de la app
$AppDir = Join-Path $Root "app"
$Venv   = Join-Path $Root "venv"
$BinDir = Join-Path $Root "bin\llama"
$Models = Join-Path $Root "models"
$Src    = Split-Path -Parent $PSScriptRoot      # carpeta del proyecto (contiene kalmiya\)
$Total  = if ($NoVision) { 8 } else { 9 }
$script:n = 0

function Step($msg) {
    $script:n++
    Write-Host ""
    Write-Host "@@STEP $($script:n)/$Total $msg"
    Write-Host "==> $msg" -ForegroundColor Cyan
}
function Download($url, $dest) {
    if (Test-Path $dest) { Write-Host "   ya existe: $(Split-Path -Leaf $dest)"; return }
    $tmp = "$dest.part"
    # Tamano total para que la ventana muestre el avance (sigue redirecciones de Hugging Face).
    $size = 0
    $head = & curl.exe -sIL $url 2>$null
    foreach ($line in $head) { if ($line -match '^content-length:\s*(\d+)') { $size = [int64]$Matches[1] } }
    Write-Host "@@DL $tmp $size $(Split-Path -Leaf $dest)"
    & curl.exe -sS -L --fail --retry 3 -C - -o $tmp $url
    if ($LASTEXITCODE -ne 0) { throw "Fallo la descarga de $url" }
    Move-Item -Force $tmp $dest
    Write-Host "@@DLDONE"
}
function StopKalmiya($dir) {
    Get-Process pythonw, python, llama-server -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force
    Start-Sleep -Milliseconds 500
}

Write-Host "Instalando Kalmiya en $Root" -ForegroundColor Green

# 0. Migrar una instalacion anterior (cuando la app se llamaba Eco): conserva modelos y configuracion.
if ((Test-Path $Old) -and -not (Test-Path $Root)) {
    Write-Host "   migrando la instalacion anterior de Eco"
    StopKalmiya $Old
    Rename-Item $Old $Root
    if (Test-Path "$Root\app\eco") { Remove-Item -Recurse -Force "$Root\app\eco" }
    if (Test-Path "$Root\logs\eco.log") { Rename-Item "$Root\logs\eco.log" "kalmiya.log" -ErrorAction SilentlyContinue }
    foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Startup"))) {
        Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $dir "Eco.lnk"), (Join-Path $dir "Desinstalar Eco.lnk")
    }
}
New-Item -ItemType Directory -Force $Root, $AppDir, $BinDir, $Models, "$Models\piper" | Out-Null

# 1. Copiar la aplicacion
Step "Copiando Kalmiya"
StopKalmiya $Root
if (Test-Path "$AppDir\kalmiya") { Remove-Item -Recurse -Force "$AppDir\kalmiya" }
Copy-Item -Recurse -Force (Join-Path $Src "kalmiya") $AppDir
Get-ChildItem "$AppDir\kalmiya" -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force
Copy-Item -Force (Join-Path $Src "requirements.txt"), (Join-Path $PSScriptRoot "uninstall.ps1") $AppDir

# 2. uv + Python (sin tocar el Python del sistema)
Step "Preparando Python"
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
}
& $uv venv --allow-existing -p 3.12 $Venv
if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el entorno de Python" }
$py = Join-Path $Venv "Scripts\python.exe"

if ($Gpu -eq "auto") {
    $Gpu = if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { "cuda" } else { "cpu" }
}
Write-Host "   backend: $Gpu"
Write-Host "@@GPU $Gpu"

Step "Instalando dependencias"
& $uv pip install -p $py -r "$AppDir\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "Fallo la instalacion de dependencias" }
if ($Gpu -eq "cuda") {
    # cuBLAS/cuDNN para Whisper en GPU (~0.2 s por frase en vez de ~4 s en CPU)
    & $uv pip install -p $py nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10"
    if ($LASTEXITCODE -ne 0) { throw "Fallo la instalacion de librerias CUDA" }
}

# 3. llama.cpp (llama-server) segun la GPU
Step "Descargando el motor de IA (llama.cpp)"
$release = Invoke-RestMethod "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10" -Headers @{ "User-Agent" = "kalmiya-installer" }
$pattern = @{ cpu = "bin-win-cpu-x64.zip"; cuda = "bin-win-cuda-12.4-x64.zip"; vulkan = "bin-win-vulkan-x64.zip" }[$Gpu]
$rel = $release | Where-Object { $_.assets | Where-Object { $_.name -like "llama-*$pattern" } } | Select-Object -First 1
if (-not $rel) { throw "No encontre un build de llama.cpp para $Gpu" }
$installedTag = if (Test-Path "$BinDir\VERSION") { Get-Content "$BinDir\VERSION" } else { "" }
if ($installedTag -ne "$($rel.tag_name)-$Gpu") {
    Get-ChildItem $BinDir -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
    $assets = @($rel.assets | Where-Object { $_.name -like "llama-*$pattern" })
    if ($Gpu -eq "cuda") { $assets += $rel.assets | Where-Object { $_.name -like "cudart-*$pattern" } }
    foreach ($a in $assets) {
        $zip = Join-Path $env:TEMP $a.name
        Download $a.browser_download_url $zip
        Expand-Archive -Force $zip $BinDir
        Remove-Item $zip
    }
    Set-Content "$BinDir\VERSION" "$($rel.tag_name)-$Gpu"
}

# 4. Modelos
Step "Descargando LFM3 (el cerebro)"
$gguf = "LFM3-1.7B-$Quant.gguf"
Download "https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF/resolve/main/$gguf" (Join-Path $Models $gguf)

if (-not $NoVision) {
    Step "Descargando LFM3-VL (para ver imagenes)"
    $vl = "https://huggingface.co/LiquidAI/LFM3-VL-2B-GGUF/resolve/main"
    Download "$vl/LFM3-VL-2B-Q4_K_M.gguf" "$Models\LFM3-VL-2B-Q4_K_M.gguf"
    Download "$vl/mmproj-LFM3-VL-2B-Q8_0.gguf" "$Models\mmproj-LFM3-VL-2B-Q8_0.gguf"
}

Step "Descargando las voces"
$piper = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
foreach ($v in @("es/es_AR/daniela/high/es_AR-daniela-high", "es/es_MX/claude/high/es_MX-claude-high",
                 "en/en_US/lessac/high/en_US-lessac-high")) {
    $name = Split-Path -Leaf $v
    Download "$piper/$v.onnx" "$Models\piper\$name.onnx"
    Download "$piper/$v.onnx.json" "$Models\piper\$name.onnx.json"
}

# En GPU NVIDIA large-v3-turbo es rapido y preciso; en CPU, "small" mantiene la latencia baja.
$whisper = if ($Gpu -eq "cuda") { "large-v3-turbo" } else { "small" }
Step "Descargando Whisper (voz a texto)"
$env:KALMIYA_HOME = $Root
& $py -c "from faster_whisper.utils import download_model; download_model('$whisper', cache_dir=r'$Models\whisper')"
if ($LASTEXITCODE -ne 0) { throw "No se pudo descargar Whisper" }

if ($Nemotron) {
    # Motor alternativo: NVIDIA NeMo-Speech.cpp (Nemotron 3.5 ASR / Transcribe). El paquete
    # completo (bin + share\model-index.json) debe quedar junto.
    Write-Host "==> Descargando NeMo-Speech.cpp" -ForegroundColor Cyan
    $nemoDir = Join-Path $Root "bin\nemo"
    $flavor = if ($Gpu -eq "cpu") { "cpu" } else { $Gpu }
    $nrel = Invoke-RestMethod "https://api.github.com/repos/NVIDIA/NeMo-Speech.cpp/releases/latest" -Headers @{ "User-Agent" = "kalmiya-installer" }
    $na = $nrel.assets | Where-Object { $_.name -like "nemo-speech-*-windows-x86_64-$flavor.zip" } | Select-Object -First 1
    $zip = Join-Path $env:TEMP $na.name
    Download $na.browser_download_url $zip
    if (Test-Path $nemoDir) { Remove-Item -Recurse -Force $nemoDir }
    Expand-Archive -Force $zip "$env:TEMP\kalmiya-nemo"
    Move-Item (Get-ChildItem "$env:TEMP\kalmiya-nemo" -Directory | Select-Object -First 1).FullName $nemoDir
    Remove-Item -Recurse -Force $zip, "$env:TEMP\kalmiya-nemo"
}

# 5. Configuracion inicial (sin pisar la del usuario) y accesos directos
Step "Creando accesos directos"
$cfgFile = Join-Path $Root "config.json"
if (-not (Test-Path $cfgFile)) {
    $ngl = if ($Gpu -eq "cpu") { 0 } else { 99 }
    @{ llm_gguf = $gguf; llm_gpu_capas = $ngl; whisper_modelo = $whisper } | ConvertTo-Json | Set-Content -Encoding ASCII $cfgFile
}
$pyw = Join-Path $Venv "Scripts\pythonw.exe"
$ico = Join-Path $Root "kalmiya.ico"
& $py -c "import sys; sys.path.insert(0, r'$AppDir'); from kalmiya.ui import make_icon; make_icon(size=256).save(r'$ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"

$shell = New-Object -ComObject WScript.Shell
function Shortcut($path) {
    $s = $shell.CreateShortcut($path)
    $s.TargetPath = $pyw
    $s.Arguments = "-m kalmiya"
    $s.WorkingDirectory = $AppDir
    $s.IconLocation = $ico
    $s.Description = "Kalmiya - asistente de voz local"
    $s.Save()
}
$programs = [Environment]::GetFolderPath("Programs")
Shortcut (Join-Path $programs "Kalmiya.lnk")
Shortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "Kalmiya.lnk")
$startup = Join-Path ([Environment]::GetFolderPath("Startup")) "Kalmiya.lnk"
if ($NoStartup) { Remove-Item -Force -ErrorAction SilentlyContinue $startup } else { Shortcut $startup }

# Benchmark: ventana de consola que mide este equipo y ofrece compartir el resultado en GitHub.
$b = $shell.CreateShortcut((Join-Path $programs "Kalmiya Benchmark.lnk"))
$b.TargetPath = Join-Path $Venv "Scripts\python.exe"
$b.Arguments = "-m kalmiya.bench --ask"
$b.WorkingDirectory = $AppDir
$b.IconLocation = $ico
$b.Description = "Mide como funciona Kalmiya en este equipo"
$b.Save()

$uninstallCmd ="-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$AppDir\uninstall.ps1`""
$u = $shell.CreateShortcut((Join-Path $programs "Desinstalar Kalmiya.lnk"))
$u.TargetPath = "powershell.exe"
$u.Arguments = $uninstallCmd
$u.IconLocation = $ico
$u.Save()

# Registro en Configuracion > Aplicaciones > Aplicaciones instaladas (y Panel de control > Programas).
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Kalmiya"
New-Item -Force $key | Out-Null
$sizeKb = [int]((Get-ChildItem $Root -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1KB)
$strings = @{
    DisplayName = "Kalmiya"; DisplayVersion = $Version; Publisher = "Kalmiya"; DisplayIcon = $ico
    InstallLocation = $Root; URLInfoAbout = "https://github.com/jcrow0526/kalmiya"
    UninstallString = "powershell.exe $uninstallCmd"; QuietUninstallString = "powershell.exe $uninstallCmd -Quiet"
}
foreach ($k in $strings.Keys) { New-ItemProperty -Force -Path $key -Name $k -Value $strings[$k] -PropertyType String | Out-Null }
foreach ($k in @("NoModify", "NoRepair")) { New-ItemProperty -Force -Path $key -Name $k -Value 1 -PropertyType DWord | Out-Null }
New-ItemProperty -Force -Path $key -Name EstimatedSize -Value $sizeKb -PropertyType DWord | Out-Null

Write-Host ""
Write-Host "@@DONE"
Write-Host "Kalmiya instalada. Manten Ctrl+Alt+Espacio y habla; Ctrl+Alt+D para dictar." -ForegroundColor Green
if (-not $NoLaunch) { Start-Process $pyw -ArgumentList "-m kalmiya" -WorkingDirectory $AppDir }
