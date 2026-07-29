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

# The taskbar identity, read from app_id.py so the shortcut and the running
# process can never drift apart. See that file for why both halves matter.
$appIdLine = Select-String -Path (Join-Path $app "app_id.py") -Pattern 'APP_ID\s*=\s*"(.+)"'
if (-not $appIdLine) { throw "Could not read APP_ID from app_id.py" }
$appId = $appIdLine.Matches[0].Groups[1].Value

# WScript.Shell cannot set System.AppUserModel.ID - it only exposes
# TargetPath/Arguments/IconLocation and friends. Without that property Windows
# identifies the shortcut by its target, which is pythonw.exe, shared with
# every other Python program: pinning from the taskbar then yields Python's
# icon, and a window that declares its own id refuses to merge with a shortcut
# pinned from the Start menu, leaving two buttons side by side.
#
# Setting it means going through IPropertyStore on the ShellLink object.
if (-not ("RendafarLnk" -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class RendafarLnk {
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    private class ShellLink { }

    [ComImport, Guid("0000010b-0000-0000-C000-000000000046"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IPersistFile {
        void GetClassID(out Guid pClassID);
        [PreserveSig] int IsDirty();
        void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, int dwMode);
        void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName,
                  [MarshalAs(UnmanagedType.Bool)] bool fRemember);
        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
        void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    private struct PROPERTYKEY { public Guid fmtid; public uint pid; }

    [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IPropertyStore {
        void GetCount(out uint cProps);
        void GetAt(uint iProp, out PROPERTYKEY pkey);
        void GetValue(ref PROPERTYKEY key, IntPtr pv);
        void SetValue(ref PROPERTYKEY key, IntPtr propvar);
        void Commit();
    }

    [DllImport("propsys.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    private static extern void PropVariantToStringAlloc(IntPtr propvar, out IntPtr ppsz);

    [DllImport("ole32.dll")]
    private static extern int PropVariantClear(IntPtr pvar);

    // {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3},5 == System.AppUserModel.ID
    private static PROPERTYKEY AppUserModelIdKey() {
        PROPERTYKEY k = new PROPERTYKEY();
        k.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
        k.pid = 5;
        return k;
    }

    // PROPVARIANT is 24 bytes on x64; 32 zeroed bytes is safely more.
    private static IntPtr NewPropVariant() {
        IntPtr pv = Marshal.AllocCoTaskMem(32);
        for (int i = 0; i < 32; i++) Marshal.WriteByte(pv, i, 0);
        return pv;
    }

    // InitPropVariantFromString is an inline helper in propvarutil.h, NOT an
    // exported entry point - calling it through P/Invoke fails with
    // EntryPointNotFoundException. Building the PROPVARIANT by hand is the
    // supported route: vt at offset 0, and the union at offset 8 on both x86
    // and x64 (a WORD plus three reserved WORDs). PropVariantClear frees the
    // string with CoTaskMemFree, which is what StringToCoTaskMemUni allocates.
    private const ushort VT_LPWSTR = 31;

    private static void SetPropVariantString(IntPtr pv, string value) {
        Marshal.WriteInt16(pv, 0, (short)VT_LPWSTR);
        Marshal.WriteIntPtr(pv, 8, Marshal.StringToCoTaskMemUni(value));
    }

    public static void SetAppId(string lnkPath, string appId) {
        IPersistFile link = (IPersistFile)new ShellLink();
        link.Load(lnkPath, 2);                       // STGM_READWRITE
        IPropertyStore store = (IPropertyStore)link;
        PROPERTYKEY key = AppUserModelIdKey();
        IntPtr pv = NewPropVariant();
        try {
            SetPropVariantString(pv, appId);
            store.SetValue(ref key, pv);
            store.Commit();
            link.Save(lnkPath, true);
        } finally {
            PropVariantClear(pv);
            Marshal.FreeCoTaskMem(pv);
        }
    }

    public static string GetAppId(string lnkPath) {
        IPersistFile link = (IPersistFile)new ShellLink();
        link.Load(lnkPath, 0);                       // STGM_READ
        IPropertyStore store = (IPropertyStore)link;
        PROPERTYKEY key = AppUserModelIdKey();
        IntPtr pv = NewPropVariant();
        IntPtr psz = IntPtr.Zero;
        try {
            store.GetValue(ref key, pv);
            PropVariantToStringAlloc(pv, out psz);
            return psz == IntPtr.Zero ? "" : Marshal.PtrToStringUni(psz);
        } catch {
            return "";
        } finally {
            if (psz != IntPtr.Zero) Marshal.FreeCoTaskMem(psz);
            PropVariantClear(pv);
            Marshal.FreeCoTaskMem(pv);
        }
    }
}
'@
}

function Set-RendafarAppId($path) {
    [RendafarLnk]::SetAppId($path, $appId)
    # Read it back rather than assume: a silent no-op here looks exactly like
    # a Windows icon-cache problem and would send the next person hunting in
    # the wrong place entirely.
    $got = [RendafarLnk]::GetAppId($path)
    if ($got -ne $appId) { throw "AppUserModelID did not stick on $path (got '$got')" }
}

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
    Set-RendafarAppId $path
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
            Set-RendafarAppId $f.FullName
            Write-Host "  repointed          $($f.FullName)"
        }
    }
}

# An already-pinned taskbar item is its own .lnk in its own folder, and it does
# not inherit anything from the Start menu copy. Without this the fix appears
# not to work until the user happens to unpin and repin.
$pinned = Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"
if (Test-Path $pinned) {
    foreach ($f in Get-ChildItem (Join-Path $pinned "*.lnk") -ErrorAction SilentlyContinue) {
        $s = $ws.CreateShortcut($f.FullName)
        # Match on what it points at, not on the file name: a pin created from
        # the taskbar can be called anything, including "pythonw.lnk".
        if ($s.TargetPath -eq $pythonw -and $s.Arguments -match [regex]::Escape($target)) {
            $s.IconLocation = "$icon,0"
            $s.Description  = "Watch and steer Blender renders"
            $s.Save()
            Set-RendafarAppId $f.FullName
            Write-Host "  fixed taskbar pin  $($f.Name)"
        }
    }
}

Write-Host ""
Write-Host "  Python : $pythonw"
Write-Host "  Icon   : $icon"
Write-Host ""
Write-Host "  Rendafar is now on your Desktop and in the Start menu."
Write-Host "  To pin it: press Start, type Rendafar, right-click it, Pin to taskbar."
