# Creates the Rendafar shortcuts: Desktop and Start Menu.
#
# Start Menu is what makes it findable in Windows search and pinnable to the
# taskbar. No admin needed - both locations are per-user.
#
# The shortcut runs pythonw.exe (no console) on rendafar_app.pyw, which starts
# the server hidden and opens the UI in its own window.

$ErrorActionPreference = "Stop"
$app = Split-Path -Parent $MyInvocation.MyCommand.Path

# Prefer the interpreter pinned in local_python.bat, so the shortcut uses the
# same Python as everything else on this machine.
$python = $null
$localBat = Join-Path $app "local_python.bat"
if (Test-Path $localBat) {
    $line = Select-String -Path $localBat -Pattern 'set\s+"PYTHON=(.+)"' -ErrorAction SilentlyContinue
    if ($line) { $python = $line.Matches[0].Groups[1].Value }
}
if (-not $python) { $python = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $python) { throw "Could not find Python. Create local_python.bat first." }

# pythonw.exe = same interpreter, no console window.
$pythonw = Join-Path (Split-Path $python -Parent) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python }

# The Qt desktop app. The old browser launcher is gone; the web UI is still
# reachable from the app's View menu and at http://127.0.0.1:8765.
$target  = Join-Path $app "rendafar_app.pyw"
$icon    = Join-Path $app "static\rendafar.ico"
if (-not (Test-Path $icon)) { throw "Icon missing. Run: python make_icon.py" }
if (-not (Test-Path $target)) { throw "Missing: $target" }

$ws = New-Object -ComObject WScript.Shell

function New-RendafarShortcut($path) {
    $s = $ws.CreateShortcut($path)
    $s.TargetPath       = $pythonw
    $s.Arguments        = "`"$target`""
    $s.WorkingDirectory = $app
    $s.IconLocation     = "$icon,0"
    $s.Description      = "Watch and steer Blender renders"
    $s.WindowStyle      = 1
    $s.Save()
    Write-Host "  created  $path"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$start   = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"

New-RendafarShortcut (Join-Path $desktop "Rendafar.lnk")
New-RendafarShortcut (Join-Path $start   "Rendafar.lnk")

# Installing the PWA makes the browser create its own "Rendafar" shortcuts that
# open the window directly. Those cannot start the server, so clicking one when
# Rendafar is closed gives "Ce site est inaccessible". Point them at the
# launcher instead, so every Rendafar icon works the same way.
$browserShortcuts = @(
    (Join-Path $desktop "Rendafar*.lnk"),
    (Join-Path $start   "Chrome Apps\Rendafar*.lnk"),
    (Join-Path $start   "Rendafar*.lnk")
) | ForEach-Object { Get-ChildItem $_ -ErrorAction SilentlyContinue }

foreach ($f in $browserShortcuts) {
    $s = $ws.CreateShortcut($f.FullName)
    if ($s.Arguments -match '--app-id=') {
        if ($f.BaseName -match '\(\d+\)$') {
            # A duplicate the browser added beside ours - just remove it.
            Remove-Item $f.FullName -Force
            Write-Host "  removed duplicate  $($f.Name)"
        } else {
            $s.TargetPath       = $pythonw
            $s.Arguments        = "`"$target`""
            $s.WorkingDirectory = $app
            $s.IconLocation     = "$icon,0"
            $s.Save()
            Write-Host "  repointed          $($f.FullName)"
        }
    }
}

Write-Host ""
Write-Host "  Python : $pythonw"
Write-Host "  Icon   : $icon"
Write-Host ""
Write-Host "  Rendafar is now on your Desktop and in the Start menu."
Write-Host "  To pin it: press Start, type Rendafar, right-click it, Pin to taskbar."
