# mcas Windows system tray host.
# Launched hidden by mcas.desktop.launcher.open_ui and the Windows Startup shortcut.
# Keep this file ASCII-only with CRLF line endings (PowerShell 5.1 compatibility).

param(
    [Parameter(Mandatory = $true)]
    [string]$Python,
    [int]$Port = 47831
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

if ($Port -lt 1024 -or $Port -gt 65535) {
    throw "Invalid port '$Port' (use 1024-65535)."
}

$Root = if ($env:MCAS_ROOT) {
    $env:MCAS_ROOT
} elseif ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA 'mcas'
} else {
    Join-Path $env:USERPROFILE 'AppData\Local\mcas'
}
$UiState = Join-Path $Root 'ui.json'
$AppDataBase = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $env:USERPROFILE 'AppData\Roaming' }
$StartupLnk = Join-Path $AppDataBase 'Microsoft\Windows\Start Menu\Programs\Startup\mcas.lnk'

function Get-UiUrl([int]$p) { return "http://127.0.0.1:$p" }

function Read-UiState {
    if (-not (Test-Path -LiteralPath $UiState)) { return $null }
    try {
        return (Get-Content -LiteralPath $UiState -Raw | ConvertFrom-Json)
    } catch {
        return $null
    }
}

# 'mcas' | 'other' | 'free'
function Get-PortOwner([int]$p) {
    try {
        $r = Invoke-RestMethod -Uri "$(Get-UiUrl $p)/health" -TimeoutSec 2 -UseBasicParsing
        if ($r.app -eq 'mcas') { return 'mcas' }
        return 'other'
    } catch {
        if (@(Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue).Count) {
            return 'other'
        }
        return 'free'
    }
}

function Invoke-McasApi([int]$p, [string]$token, [string]$method, [string]$path, $bodyObj, [int]$timeoutSec) {
    $headers = @{ 'X-Mcas-Token' = $token }
    $uri = "$(Get-UiUrl $p)$path"
    if ($method -eq 'POST') {
        $json = if ($null -ne $bodyObj) { ConvertTo-Json -InputObject $bodyObj -Depth 6 -Compress } else { '{}' }
        $bytes = [Text.Encoding]::UTF8.GetBytes($json)
        return Invoke-RestMethod -Uri $uri -Method Post -Headers $headers -Body $bytes `
            -ContentType 'application/json; charset=utf-8' -TimeoutSec $timeoutSec -UseBasicParsing
    }
    return Invoke-RestMethod -Uri $uri -Method Get -Headers $headers -TimeoutSec $timeoutSec -UseBasicParsing
}

function Get-HttpErrorMessage($errRecord) {
    try {
        $resp = $errRecord.Exception.Response
        if ($resp) {
            $sr = New-Object IO.StreamReader ($resp.GetResponseStream()), ([Text.Encoding]::UTF8)
            $body = $sr.ReadToEnd()
            $sr.Close()
            if ($body) {
                $obj = $body | ConvertFrom-Json
                if ($obj.error) { return [string]$obj.error }
            }
        }
    } catch { }
    return [string]$errRecord.Exception.Message
}

function New-TrayIconImage {
    $bmp = New-Object Drawing.Bitmap 32, 32
    $g = [Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = 'AntiAlias'
    $g.TextRenderingHint = 'AntiAliasGridFit'
    $brush = New-Object Drawing.SolidBrush ([Drawing.Color]::FromArgb(79, 70, 229))
    $g.FillEllipse($brush, 0, 0, 31, 31)
    $font = New-Object Drawing.Font 'Segoe UI', 19, ([Drawing.FontStyle]::Bold), ([Drawing.GraphicsUnit]::Pixel)
    $sf = New-Object Drawing.StringFormat
    $sf.Alignment = 'Center'
    $sf.LineAlignment = 'Center'
    $g.DrawString('m', $font, [Drawing.Brushes]::White, (New-Object Drawing.RectangleF 0, -1, 32, 32), $sf)
    $brush.Dispose()
    $font.Dispose()
    $sf.Dispose()
    $g.Dispose()
    $icon = [Drawing.Icon]::FromHandle($bmp.GetHicon())
    $bmp.Dispose()
    return $icon
}

function Invoke-TrayAction([scriptblock]$action) {
    try {
        $m = & $action
        if ($m -and [string]$m -ne '') {
            $txt = [string]$m
            if ($txt.Length -gt 240) { $txt = $txt.Substring(0, 237) + '...' }
            $script:Tray.ShowBalloonTip(3500, 'mcas', $txt, 'Info')
        }
    } catch {
        $errTxt = Get-HttpErrorMessage $_
        if (-not $errTxt) { $errTxt = 'Action failed.' }
        if ($errTxt.Length -gt 240) { $errTxt = $errTxt.Substring(0, 237) + '...' }
        $script:Tray.ShowBalloonTip(5000, 'mcas', $errTxt, 'Error')
    }
}

function Update-TrayMenu($menu) {
    $menu.Items.Clear()

    $open = New-Object Windows.Forms.ToolStripMenuItem 'Open mcas'
    $open.Font = New-Object Drawing.Font $open.Font, ([Drawing.FontStyle]::Bold)
    $open.add_Click({ Start-Process $script:UiUrl })
    [void]$menu.Items.Add($open)
    [void]$menu.Items.Add((New-Object Windows.Forms.ToolStripSeparator))

    $st = $null
    try {
        $st = Invoke-McasApi $script:UiPort $script:UiToken 'GET' '/api/status' $null 2
    } catch { }

    if ($st -and $null -ne $st.autosync) {
        $enabled = [bool]$st.autosync.enabled
        $label = if ($enabled) { 'Auto-Sync: ON' } else { 'Auto-Sync: OFF' }
        $autoSync = New-Object Windows.Forms.ToolStripMenuItem $label
        $autoSync.Checked = $enabled
        $autoSync.Tag = (-not $enabled)
        $autoSync.add_Click({
            $next = [bool]$this.Tag
            Invoke-TrayAction {
                $r = Invoke-McasApi $script:UiPort $script:UiToken 'POST' '/api/autosync' @{ enabled = $next } 10
                if ($r.message) { return [string]$r.message }
                if ($next) { return 'Auto-Sync enabled.' }
                return 'Auto-Sync disabled.'
            }
        })
        [void]$menu.Items.Add($autoSync)
    } else {
        $autoSync = New-Object Windows.Forms.ToolStripMenuItem 'Auto-Sync: unavailable'
        $autoSync.Enabled = $false
        [void]$menu.Items.Add($autoSync)
    }

    $syncNow = New-Object Windows.Forms.ToolStripMenuItem 'Sync Now'
    $syncNow.Enabled = ($null -ne $st)
    $syncNow.add_Click({
        Invoke-TrayAction {
            $r = Invoke-McasApi $script:UiPort $script:UiToken 'POST' '/api/autosync' @{ runNow = $true } 30
            if ($r.message) { return [string]$r.message }
            return 'Auto-Sync cycle finished.'
        }
    })
    [void]$menu.Items.Add($syncNow)

    [void]$menu.Items.Add((New-Object Windows.Forms.ToolStripSeparator))

    $autoOn = if ($st -and $null -ne $st.autostart) { [bool]$st.autostart } else { Test-Path -LiteralPath $StartupLnk }
    $auto = New-Object Windows.Forms.ToolStripMenuItem 'Start with Windows'
    $auto.Checked = $autoOn
    $auto.Tag = (-not $autoOn)
    $auto.add_Click({
        $next = [bool]$this.Tag
        Invoke-TrayAction {
            $r = Invoke-McasApi $script:UiPort $script:UiToken 'POST' '/api/autostart' @{ on = $next } 15
            if ($r.message) { return [string]$r.message }
            if ($next) { return 'mcas will start in the tray when you sign in to Windows.' }
            return 'mcas will not start with Windows.'
        }
    })
    [void]$menu.Items.Add($auto)

    $quit = New-Object Windows.Forms.ToolStripMenuItem 'Quit mcas'
    $quit.add_Click({
        $script:UiQuit = $true
        try { Invoke-McasApi $script:UiPort $script:UiToken 'POST' '/api/quit' @{} 2 | Out-Null } catch { }
    })
    [void]$menu.Items.Add($quit)
}

Add-Type -AssemblyName System.Windows.Forms, System.Drawing

if ((Get-PortOwner $Port) -eq 'free') {
    $oldState = Read-UiState
    if ($oldState -and ([int]$oldState.port -eq $Port)) {
        Remove-Item -LiteralPath $UiState -Force -ErrorAction SilentlyContinue
    }
}

$psi = New-Object Diagnostics.ProcessStartInfo
$psi.FileName = $Python
$psi.Arguments = "-B -m mcas __serve --port $Port"
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true

$pyProc = [Diagnostics.Process]::Start($psi)
if (-not $pyProc) { throw 'Failed to launch mcas Python server.' }

$script:UiPort = $Port
$script:UiUrl = Get-UiUrl $Port
$script:UiToken = $null
$script:ServerPid = 0
$script:UiQuit = $false
$script:Tray = $null
$script:UiTimer = $null
$trayIcon = $null

try {
    # Wait for mcas __serve to write ui.json and begin serving /health.
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        if ($pyProc.HasExited) {
            throw "mcas Python server exited prematurely with code $($pyProc.ExitCode)."
        }
        $st = Read-UiState
        if ($st -and [int]$st.port -eq $Port -and $st.token) {
            if ((Get-PortOwner $Port) -eq 'mcas') {
                $script:UiToken = [string]$st.token
                if ($st.pid) { $script:ServerPid = [int]$st.pid }
                break
            }
        }
        Start-Sleep -Milliseconds 150
    }
    if (-not $script:UiToken) {
        throw "Timed out waiting for mcas server on port $Port."
    }

    $trayIcon = New-TrayIconImage
    $script:Tray = New-Object Windows.Forms.NotifyIcon
    $script:Tray.Icon = $trayIcon
    $tip = "mcas - $($script:UiUrl)"
    if ($tip.Length -gt 63) { $tip = $tip.Substring(0, 63) }
    $script:Tray.Text = $tip

    $menu = New-Object Windows.Forms.ContextMenuStrip
    Update-TrayMenu $menu
    # Refill on every open; reset Cancel so WinForms opens the freshly populated strip.
    $menu.add_Opening({ param($s, $e) Update-TrayMenu $s; $e.Cancel = $false })
    $script:Tray.ContextMenuStrip = $menu
    $script:Tray.add_MouseClick({
        param($s, $e)
        if ($e.Button -eq 'Left') { Start-Process $script:UiUrl }
    })
    $script:Tray.Visible = $true

    $script:PyProc = $pyProc
    $script:UiTimer = New-Object Windows.Forms.Timer
    $script:UiTimer.Interval = 500
    $script:UiTimer.add_Tick({
        $srvDead = ($script:ServerPid -gt 0) -and ((Get-Process -Id $script:ServerPid -ErrorAction SilentlyContinue) -eq $null)
        if ($script:UiQuit -or $script:PyProc.HasExited -or $srvDead) {
            $script:UiTimer.Stop()
            [Windows.Forms.Application]::ExitThread()
        }
    })
    $script:UiTimer.Start()
    [Windows.Forms.Application]::Run()
} finally {
    if ($script:UiTimer) { $script:UiTimer.Dispose() }
    if ($script:Tray) {
        $script:Tray.Visible = $false
        $script:Tray.Dispose()
    }
    if ($trayIcon) { $trayIcon.Dispose() }

    if ($pyProc -and -not $pyProc.HasExited) {
        if ($script:UiToken) {
            try { Invoke-McasApi $Port $script:UiToken 'POST' '/api/quit' @{} 2 | Out-Null } catch { }
        }
        if (-not $pyProc.WaitForExit(2000)) {
            try { $pyProc.Kill() } catch { }
        }
    } elseif ($script:ServerPid -gt 0 -and (Get-Process -Id $script:ServerPid -ErrorAction SilentlyContinue)) {
        if ($script:UiToken) {
            try { Invoke-McasApi $Port $script:UiToken 'POST' '/api/quit' @{} 2 | Out-Null } catch { }
        }
        Start-Sleep -Milliseconds 300
        if (Get-Process -Id $script:ServerPid -ErrorAction SilentlyContinue) {
            Stop-Process -Id $script:ServerPid -Force -ErrorAction SilentlyContinue
        }
    }
    if ($pyProc) {
        $curState = Read-UiState
        if ($curState -and (([int]$curState.pid -eq $pyProc.Id) -or ($script:ServerPid -gt 0 -and [int]$curState.pid -eq $script:ServerPid))) {
            Remove-Item -LiteralPath $UiState -Force -ErrorAction SilentlyContinue
        }
        $pyProc.Dispose()
    }
}
