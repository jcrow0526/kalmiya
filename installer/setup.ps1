# Ventana del instalador de Kalmiya (WPF). Ejecuta install.ps1 en segundo plano y muestra el avance.
# Se guarda con BOM UTF-8 para que Windows PowerShell 5.1 lea bien los acentos.
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

$Here   = $PSScriptRoot
$Assets = Join-Path $Here "assets"
$Root   = Join-Path $env:LOCALAPPDATA "Kalmiya"
$LogOut = Join-Path $env:TEMP "kalmiya-install.log"
$LogErr = Join-Path $env:TEMP "kalmiya-install.err"
$Accent = "#7CFF2B"

[xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Instalar Kalmiya" Width="760" Height="480" WindowStyle="None" AllowsTransparency="True"
        Background="Transparent" WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        FontFamily="Segoe UI Variable Display, Segoe UI">
  <Window.Resources>
    <Style x:Key="Primary" TargetType="Button">
      <Setter Property="Foreground" Value="#0B0C0E"/>
      <Setter Property="FontSize" Value="15"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" CornerRadius="14" Background="$Accent" Padding="28,11">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="b" Property="Opacity" Value="0.88"/></Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="Ghost" TargetType="Button">
      <Setter Property="Foreground" Value="#9A9CA8"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" CornerRadius="10" Background="Transparent" Padding="10,6">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="b" Property="Background" Value="#1FFFFFFF"/></Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style TargetType="CheckBox">
      <Setter Property="Foreground" Value="#D6D7DE"/>
      <Setter Property="FontSize" Value="13.5"/>
      <Setter Property="Margin" Value="0,5"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="CheckBox">
            <StackPanel Orientation="Horizontal">
              <Border x:Name="box" Width="20" Height="20" CornerRadius="6" BorderThickness="1.5"
                      BorderBrush="#4A4C56" Background="#14FFFFFF" Margin="0,0,10,0">
                <TextBlock x:Name="tick" Text="&#x2713;" Foreground="#0B0C0E" FontWeight="Bold" FontSize="13"
                           HorizontalAlignment="Center" VerticalAlignment="Center" Visibility="Collapsed"/>
              </Border>
              <ContentPresenter VerticalAlignment="Center"/>
            </StackPanel>
            <ControlTemplate.Triggers>
              <Trigger Property="IsChecked" Value="True">
                <Setter TargetName="box" Property="Background" Value="$Accent"/>
                <Setter TargetName="box" Property="BorderBrush" Value="$Accent"/>
                <Setter TargetName="tick" Property="Visibility" Value="Visible"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
  </Window.Resources>

  <Border CornerRadius="26" Margin="16" BorderThickness="1" BorderBrush="#2E3038">
    <Border.Background>
      <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
        <GradientStop Color="#FA0F1014" Offset="0"/>
        <GradientStop Color="#FC161820" Offset="1"/>
      </LinearGradientBrush>
    </Border.Background>
    <Border.Effect><DropShadowEffect BlurRadius="28" ShadowDepth="6" Opacity="0.55" Color="Black"/></Border.Effect>
    <Grid>
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="300"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>

      <!-- brillo detras del orbe -->
      <Ellipse Width="260" Height="260" Opacity="0.35" IsHitTestVisible="False">
        <Ellipse.Fill>
          <RadialGradientBrush>
            <GradientStop Color="$Accent" Offset="0"/>
            <GradientStop Color="#00000000" Offset="1"/>
          </RadialGradientBrush>
        </Ellipse.Fill>
      </Ellipse>
      <Image x:Name="Orb" Width="250" Height="250" RenderOptions.BitmapScalingMode="HighQuality"/>

      <Button x:Name="CloseBtn" Grid.Column="1" Style="{StaticResource Ghost}" Content="&#x2715;"
              HorizontalAlignment="Right" VerticalAlignment="Top" Margin="0,14,16,0"/>

      <StackPanel Grid.Column="1" Margin="10,44,40,30">
        <TextBlock Text="Kalmiya" Foreground="#F4F5F8" FontSize="40" FontWeight="Bold"/>
        <TextBlock x:Name="Subtitle" Text="Tu asistente de voz. Todo corre en tu PC."
                   Foreground="#9A9CA8" FontSize="15" Margin="0,2,0,22"/>

        <!-- 1. inicio -->
        <StackPanel x:Name="StartPanel">
          <Border CornerRadius="16" Background="#0FFFFFFF" BorderBrush="#1AFFFFFF" BorderThickness="1" Padding="18,14">
            <StackPanel>
              <TextBlock Foreground="#C9CAD3" FontSize="13.5" TextWrapping="Wrap" LineHeight="21">
                Se descargan los modelos de IA locales: LFM3 para entender y actuar, Whisper para escucharte
                y una voz natural para responderte. Unos 5 GB en total.
              </TextBlock>
            </StackPanel>
          </Border>
          <StackPanel Margin="2,16,0,0">
            <CheckBox x:Name="Vision" IsChecked="True" Content="Ver imágenes de la pantalla con LFM3-VL  (+1.2 GB)"/>
            <CheckBox x:Name="Startup" IsChecked="True" Content="Iniciar Kalmiya con Windows"/>
          </StackPanel>
          <Button x:Name="InstallBtn" Style="{StaticResource Primary}" Content="Instalar" HorizontalAlignment="Left"
                  Margin="0,22,0,0"/>
        </StackPanel>

        <!-- 2. progreso -->
        <StackPanel x:Name="ProgressPanel" Visibility="Collapsed">
          <TextBlock x:Name="StepCount" Foreground="#7D7F8B" FontSize="12.5"/>
          <TextBlock x:Name="StepText" Foreground="#F4F5F8" FontSize="19" FontWeight="SemiBold" Margin="0,4,0,16"
                     TextWrapping="Wrap"/>
          <Border CornerRadius="5" Height="10" Background="#1AFFFFFF">
            <Grid x:Name="Track" ClipToBounds="True">
              <Border x:Name="Fill" CornerRadius="5" HorizontalAlignment="Left" Width="0" Background="$Accent">
                <Border.Effect><DropShadowEffect Color="$Accent" BlurRadius="14" ShadowDepth="0" Opacity="0.8"/></Border.Effect>
              </Border>
            </Grid>
          </Border>
          <Grid Margin="0,10,0,0">
            <TextBlock x:Name="Detail" Foreground="#9A9CA8" FontSize="12.5"/>
            <TextBlock x:Name="Percent" Foreground="#C9CAD3" FontSize="12.5" HorizontalAlignment="Right"/>
          </Grid>
          <Button x:Name="LogBtn" Style="{StaticResource Ghost}" Content="Ver detalles" HorizontalAlignment="Left"
                  Margin="-10,14,0,0"/>
          <TextBox x:Name="Log" Visibility="Collapsed" Height="120" Margin="0,4,0,0" IsReadOnly="True"
                   Background="#40000000" Foreground="#8E909C" BorderThickness="0" FontFamily="Cascadia Mono, Consolas"
                   FontSize="11" VerticalScrollBarVisibility="Auto" TextWrapping="Wrap" Padding="8"/>
        </StackPanel>

        <!-- 3. listo / error -->
        <StackPanel x:Name="DonePanel" Visibility="Collapsed">
          <TextBlock x:Name="DoneTitle" Text="Todo listo" Foreground="#F4F5F8" FontSize="24" FontWeight="SemiBold"/>
          <TextBlock x:Name="DoneText" Foreground="#C9CAD3" FontSize="14" TextWrapping="Wrap" Margin="0,8,0,0"
                     LineHeight="22"
                     Text="Mantén Ctrl + Alt + Espacio, habla y suelta. Kalmiya vive junto al reloj; la primera vez te presentará lo que puede hacer."/>
          <StackPanel Orientation="Horizontal" Margin="0,24,0,0">
            <Button x:Name="OpenBtn" Style="{StaticResource Primary}" Content="Abrir Kalmiya"/>
            <Button x:Name="FinishBtn" Style="{StaticResource Ghost}" Content="Cerrar" Margin="10,0,0,0"/>
          </StackPanel>
        </StackPanel>
      </StackPanel>
    </Grid>
  </Border>
</Window>
"@

$win = [Windows.Markup.XamlReader]::Load((New-Object System.Xml.XmlNodeReader $xaml))
$ui = @{}
foreach ($n in "Orb","CloseBtn","Subtitle","StartPanel","Vision","Startup","InstallBtn","ProgressPanel","StepCount",
               "StepText","Track","Fill","Detail","Percent","LogBtn","Log","DonePanel","DoneTitle","DoneText","OpenBtn","FinishBtn") {
    $ui[$n] = $win.FindName($n)
}
try { $win.Icon = [Windows.Media.Imaging.BitmapFrame]::Create([Uri](Join-Path $Assets "kalmiya.ico")) } catch {}

# --- orbe animado (fotogramas renderizados con el mismo motor que la app; ida y vuelta)
$frames = @(Get-ChildItem $Assets -Filter "orb_*.png" | Sort-Object Name | ForEach-Object {
    $b = New-Object Windows.Media.Imaging.BitmapImage
    $b.BeginInit(); $b.UriSource = [Uri]$_.FullName; $b.CacheOption = "OnLoad"; $b.EndInit(); $b.Freeze(); $b
})
$script:fi = 0; $script:dir = 1
$orbTimer = New-Object Windows.Threading.DispatcherTimer
$orbTimer.Interval = [TimeSpan]::FromMilliseconds(55)
$orbTimer.Add_Tick({
    if ($frames.Count -eq 0) { return }
    $ui.Orb.Source = $frames[$script:fi]
    $script:fi += $script:dir
    if ($script:fi -ge $frames.Count - 1 -or $script:fi -le 0) { $script:dir = -$script:dir }
})
$orbTimer.Start()

$win.Add_MouseLeftButtonDown({ try { $win.DragMove() } catch {} })
# Lanzada desde KalmiyaSetup.exe, Windows la deja detras de las demas ventanas: traerla al frente.
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class Fg {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
}
"@
$win.Topmost = $true
$win.Add_ContentRendered({
    $h = (New-Object Windows.Interop.WindowInteropHelper $win).Handle
    [Fg]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero); [Fg]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)  # Alt: permite tomar el foco
    [Fg]::SetForegroundWindow($h) | Out-Null
    $win.Activate() | Out-Null
    $t = New-Object Windows.Threading.DispatcherTimer
    $t.Interval = [TimeSpan]::FromMilliseconds(1500)
    $t.Add_Tick({ $win.Topmost = $false; $this.Stop() })
    $t.Start()
})
$ui.CloseBtn.Add_Click({
    if ($script:proc -and -not $script:proc.HasExited) {
        $r = [Windows.MessageBox]::Show("La instalación sigue en curso. ¿Cancelarla?", "Kalmiya", "YesNo", "Question")
        if ($r -ne "Yes") { return }
        try { Stop-Process -Id $script:proc.Id -Force } catch {}
    }
    $win.Close()
})
$ui.LogBtn.Add_Click({
    $show = $ui.Log.Visibility -ne "Visible"
    $ui.Log.Visibility = if ($show) { "Visible" } else { "Collapsed" }
    $ui.LogBtn.Content = if ($show) { "Ocultar detalles" } else { "Ver detalles" }
})
$ui.FinishBtn.Add_Click({ $win.Close() })
$ui.OpenBtn.Add_Click({
    $pyw = Join-Path $Root "venv\Scripts\pythonw.exe"
    if ($script:failed) { Start-Process notepad.exe $LogOut } else { Start-Process $pyw -ArgumentList "-m kalmiya" -WorkingDirectory (Join-Path $Root "app") }
    $win.Close()
})

function Set-Progress([double]$frac) {
    $frac = [Math]::Max(0, [Math]::Min(1, $frac))
    $ui.Fill.Width = [Math]::Max(10, $ui.Track.ActualWidth * $frac)
    $ui.Percent.Text = "{0:0}%" -f ($frac * 100)
}

# --- instalar
$script:proc = $null; $script:read = 0; $script:step = 0; $script:total = 9; $script:dl = $null
$script:failed = $false; $script:done = $false
$ui.InstallBtn.Add_Click({
    $ui.StartPanel.Visibility = "Collapsed"
    $ui.ProgressPanel.Visibility = "Visible"
    $ui.Subtitle.Text = "Instalando... puedes seguir usando tu PC."
    $ui.StepText.Text = "Preparando"
    Remove-Item -Force -ErrorAction SilentlyContinue $LogOut, $LogErr
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$(Join-Path $Here 'install.ps1')`"", "-NoLaunch")
    if (-not $ui.Vision.IsChecked) { $argList += "-NoVision" }
    if (-not $ui.Startup.IsChecked) { $argList += "-NoStartup" }
    $script:proc = Start-Process powershell.exe -ArgumentList $argList -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $LogOut -RedirectStandardError $LogErr
    $poll.Start()
})

$poll = New-Object Windows.Threading.DispatcherTimer
$poll.Interval = [TimeSpan]::FromMilliseconds(300)
$poll.Add_Tick({
    if (Test-Path $LogOut) {
        # El instalador tiene el archivo abierto para escribir: hay que abrirlo compartido.
        $fs = New-Object IO.FileStream($LogOut, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        $sr = New-Object IO.StreamReader($fs)
        $text = $sr.ReadToEnd(); $sr.Close()
        if ($text.Length -gt $script:read) {
            $new = $text.Substring($script:read); $script:read = $text.Length
            foreach ($line in $new -split "`r?`n") {
                if ($line -match '^@@STEP (\d+)/(\d+) (.+)$') {
                    $script:step = [int]$Matches[1]; $script:total = [int]$Matches[2]; $script:dl = $null
                    $ui.StepText.Text = $Matches[3]
                    $ui.StepCount.Text = "Paso $($script:step) de $($script:total)"
                    $ui.Detail.Text = ""
                } elseif ($line -match '^@@DL (\S+) (\d+) (.+)$') {
                    $script:dl = @{ path = $Matches[1]; size = [int64]$Matches[2]; name = $Matches[3] }
                } elseif ($line -match '^@@DLDONE') {
                    $script:dl = $null; $ui.Detail.Text = ""
                } elseif ($line -match '^@@DONE') {
                    $script:done = $true
                } elseif ($line.Trim() -and $line -notmatch '^@@') {
                    $ui.Log.AppendText($line + "`n"); $ui.Log.ScrollToEnd()
                }
            }
        }
    }
    $frac = 0.0
    if ($script:dl -and (Test-Path $script:dl.path) -and $script:dl.size -gt 0) {
        $got = (Get-Item $script:dl.path).Length
        $frac = [Math]::Min(1, $got / $script:dl.size)
        $ui.Detail.Text = "{0}  -  {1:N0} de {2:N0} MB" -f $script:dl.name, ($got / 1MB), ($script:dl.size / 1MB)
    }
    if ($script:step -gt 0) { Set-Progress ((($script:step - 1) + $frac) / $script:total) }

    if ($script:proc -and $script:proc.HasExited) {
        $poll.Stop()
        $ui.ProgressPanel.Visibility = "Collapsed"
        $ui.DonePanel.Visibility = "Visible"
        if ($script:done) {
            $ui.Subtitle.Text = "Instalada correctamente."
        } else {
            $script:failed = $true
            $err = if (Test-Path $LogErr) { (Get-Content $LogErr -Tail 6) -join "`n" } else { "" }
            $ui.DoneTitle.Text = "La instalación no terminó"
            $ui.DoneText.Text = "Revisa tu conexión a internet e intenta de nuevo.`n`n$err"
            $ui.OpenBtn.Content = "Ver registro"
        }
    }
})

$win.ShowDialog() | Out-Null
