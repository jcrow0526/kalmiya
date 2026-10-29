; Genera KalmiyaSetup.exe (un solo archivo) con Inno Setup 6:  iscc installer\kalmiya.iss
; El .exe es pequeno: extrae la app y abre la ventana del instalador, que descarga los modelos.
[Setup]
AppName=Kalmiya
AppVersion=0.2.0
AppPublisher=Kalmiya
; Fuera de %LOCALAPPDATA%\Kalmiya: si esa carpeta ya existiera, install.ps1 no migraria una instalacion de Eco.
DefaultDirName={localappdata}\Programs\KalmiyaSetup
PrivilegesRequired=lowest
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
OutputDir=..\dist
OutputBaseFilename=KalmiyaSetup
SetupIconFile=assets\kalmiya.ico
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
Uninstallable=no

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
Source: "..\kalmiya\*"; DestDir: "{app}\kalmiya"; Excludes: "__pycache__"; Flags: recursesubdirs
Source: "..\requirements.txt"; DestDir: "{app}"
Source: "install.ps1"; DestDir: "{app}\installer"
Source: "setup.ps1"; DestDir: "{app}\installer"
Source: "uninstall.ps1"; DestDir: "{app}\installer"
Source: "assets\*"; DestDir: "{app}\installer\assets"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\installer\setup.ps1"""; Flags: nowait runhidden
