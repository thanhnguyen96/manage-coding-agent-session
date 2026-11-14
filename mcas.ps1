# mcas (Manage Coding Agent Session) - Windows CLI wrapper, tray host, and lifecycle manager.
#
# Installs per user into %LOCALAPPDATA%\mcas (override with MCAS_ROOT):
#   app\mcas.ps1, app\mcas.py, app\ui.html
#   bin\mcas.cmd, bin\manage-coding-agent-session.cmd
#
# Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less files as ANSI.

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$Version      = '0.1.0'
$Root         = if ($env:MCAS_ROOT) { $env:MCAS_ROOT } else { Join-Path $env:LOCALAPPDATA 'mcas' }
$RepoRaw      = 'https://raw.githubusercontent.com/thanhnguyen96/manage-coding-agent-session'
$DefaultPort  = 47831
$UiState      = Join-Path $Root 'ui.json'
$StartupLnk   = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\mcas.lnk'
$StartMenuLnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\mcas.lnk'

# ---------------------------------------------------------------- Python 3

$script:PythonSpec = $null

function Get-PythonSpec {
    if ($script:PythonSpec) { return $script:PythonSpec }
    foreach ($cand in @(
        @{ Exe = 'python'; Prefix = @() },
        @{ Exe = 'py';     Prefix = @('-3') }
    )) {
        $cmd = Get-Command $cand.Exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $checkArgs = @($cand.Prefix) + @('-c', 'import sys; print(sys.executable) if sys.version_info >= (3, 8) else sys.exit(1)')
            $out = & $cmd.Source @checkArgs 2>$null
            if ($LASTEXITCODE -eq 0) {
                $realExe = [string]($out | Select-Object -Last 1)
                if ($realExe) { $realExe = $realExe.Trim() }
                if ($realExe -and (Test-Path -LiteralPath $realExe)) {
                    $script:PythonSpec = @{ Exe = $realExe; Prefix = @() }
                    return $script:PythonSpec
                }
                $script:PythonSpec = @{ Exe = $cmd.Source; Prefix = $cand.Prefix }
                return $script:PythonSpec
            }
        } catch {
        } finally {
            $ErrorActionPreference = $prevEap
        }
    }
    throw 'Python 3 is required. Install Python 3 (https://www.python.org or "winget install Python.Python.3.12") and ensure "python" or "py" is on PATH.'
}

function Quote-WinArg([string]$arg) {
    if ($null -eq $arg -or $arg -eq '') { return '""' }
    if ($arg -match '[\s"]') {
        return '"' + ($arg -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
    }
    return $arg
}

function Invoke-McasPy([string[]]$pyArgs) {
    $py = Get-PythonSpec
    $pyScript = Join-Path $PSScriptRoot 'mcas.py'
    if (-not (Test-Path -LiteralPath $pyScript)) {
        throw "Missing core engine: $pyScript"
    }
    $allArgs = @()
    if ($py.Prefix.Count -gt 0) { $allArgs += $py.Prefix }
    $allArgs += '-B'
    $allArgs += $pyScript
    if ($pyArgs -and $pyArgs.Count -gt 0) { $allArgs += $pyArgs }

    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $py.Exe @allArgs
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

# ---------------------------------------------------------------- Web UI & HTTP helpers

function Get-UiUrl([int]$port) { return "http://127.0.0.1:$port" }

function Read-UiState {
    if (-not (Test-Path -LiteralPath $UiState)) { return $null }
    try {
        return (Get-Content -LiteralPath $UiState -Raw | ConvertFrom-Json)
    } catch {
        return $null
    }
}

# 'mcas' | 'other' (port taken by another program) | 'free'
function Get-PortOwner([int]$port) {
    try {
        $r = Invoke-RestMethod -Uri "$(Get-UiUrl $port)/health" -TimeoutSec 2 -UseBasicParsing
        if ($r.app -eq 'mcas') { return 'mcas' }
        return 'other'
    } catch {
        if (@(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).Count) {
            return 'other'
        }
        return 'free'
    }
}

function Invoke-McasApi([int]$port, [string]$token, [string]$method, [string]$path, $bodyObj, [int]$timeoutSec) {
    $headers = @{ 'X-Mcas-Token' = $token }
    $uri = "$(Get-UiUrl $port)$path"
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

function Get-UiPort($rest) {
    $p = $null
    for ($i = 0; $i -lt $rest.Count; $i++) {
        $arg = [string]$rest[$i]
        if ($arg -eq '--port') {
            if ($i + 1 -ge $rest.Count) { throw 'Missing value for --port.' }
            $p = [string]$rest[$i + 1]
            break
        }
        if ($arg -like '--port=*') {
            $p = $arg.Substring(7)
            break
        }
    }
    if (-not $p) {
        $p = if ($env:MCAS_PORT) { $env:MCAS_PORT } else { [string]$DefaultPort }
    }
    $n = 0
    if (-not [int]::TryParse($p, [ref]$n) -or $n -lt 1024 -or $n -gt 65535) {
        throw "Invalid port '$p' (use 1024-65535)."
    }
    return $n
}

function Get-PositionalArgs($rest, [string[]]$valueFlags) {
    $pos = @()
    for ($i = 0; $i -lt $rest.Count; $i++) {
        $a = [string]$rest[$i]
        if ($valueFlags -contains $a) { $i++; continue }
        if ($a -like '-*') { continue }
        $pos += $a
    }
    return $pos
}

# ---------------------------------------------------------------- Autostart

function Test-Autostart { return (Test-Path -LiteralPath $StartupLnk) }

function Set-Autostart([bool]$on, [int]$port) {
    if (-not $on) {
        Remove-Item -LiteralPath $StartupLnk -Force -ErrorAction SilentlyContinue
        return 'mcas will not start with Windows.'
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $StartupLnk) | Out-Null
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($StartupLnk)
    $s.TargetPath = 'powershell.exe'
    $portArg = if ($port -and $port -ne $DefaultPort) { " --port $port" } else { '' }
    $s.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`" ui --no-browser$portArg"
    $s.Description = 'mcas tray icon and Auto-Sync'
    $s.WindowStyle = 7
    $s.Save()
    return 'mcas will start in the tray when you sign in to Windows.'
}

# ---------------------------------------------------------------- Tray icon + Python server

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

    $auto = New-Object Windows.Forms.ToolStripMenuItem 'Start with Windows'
    $auto.Checked = Test-Autostart
    $auto.add_Click({
        $on = -not (Test-Autostart)
        Invoke-TrayAction { Set-Autostart $on $script:UiPort }
    })
    [void]$menu.Items.Add($auto)

    $quit = New-Object Windows.Forms.ToolStripMenuItem 'Quit mcas'
    $quit.add_Click({
        $script:UiQuit = $true
        try { Invoke-McasApi $script:UiPort $script:UiToken 'POST' '/api/quit' @{} 2 | Out-Null } catch { }
    })
    [void]$menu.Items.Add($quit)
}

function Start-UiTrayServer([int]$port) {
    if ($port -lt 1024 -or $port -gt 65535) { throw "Invalid port '$port' (use 1024-65535)." }
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing

    $py = Get-PythonSpec
    $mcasPy = Join-Path $PSScriptRoot 'mcas.py'
    $uiHtml = Join-Path $PSScriptRoot 'ui.html'
    if (-not (Test-Path -LiteralPath $mcasPy)) { throw "Missing core engine: $mcasPy" }
    if (-not (Test-Path -LiteralPath $uiHtml)) { throw "Missing UI file: $uiHtml" }

    if ((Get-PortOwner $port) -eq 'free') {
        $oldState = Read-UiState
        if ($oldState -and ([int]$oldState.port -eq $port)) {
            Remove-Item -LiteralPath $UiState -Force -ErrorAction SilentlyContinue
        }
    }

    $pyArgs = @()
    if ($py.Prefix.Count -gt 0) { $pyArgs += $py.Prefix }
    $pyArgs += @('-B', $mcasPy, '__serve', '--port', [string]$port, '--ui-html', $uiHtml)

    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $py.Exe
    $psi.Arguments = (($pyArgs | ForEach-Object { Quote-WinArg ([string]$_) }) -join ' ')
    $psi.WorkingDirectory = $PSScriptRoot
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    $pyProc = [Diagnostics.Process]::Start($psi)
    if (-not $pyProc) { throw 'Failed to launch mcas Python server.' }

    $script:UiPort = $port
    $script:UiUrl = Get-UiUrl $port
    $script:UiToken = $null
    $script:ServerPid = 0
    $script:UiQuit = $false
    $script:Tray = $null
    $script:UiTimer = $null
    $trayIcon = $null

    try {
        # Wait for mcas.py to write ui.json and begin serving /health.
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Date) -lt $deadline) {
            if ($pyProc.HasExited) {
                throw "mcas Python server exited prematurely with code $($pyProc.ExitCode)."
            }
            $st = Read-UiState
            if ($st -and [int]$st.port -eq $port -and $st.token) {
                if ((Get-PortOwner $port) -eq 'mcas') {
                    $script:UiToken = [string]$st.token
                    if ($st.pid) { $script:ServerPid = [int]$st.pid }
                    break
                }
            }
            Start-Sleep -Milliseconds 150
        }
        if (-not $script:UiToken) {
            throw "Timed out waiting for mcas server on port $port."
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
                try { Invoke-McasApi $port $script:UiToken 'POST' '/api/quit' @{} 2 | Out-Null } catch { }
            }
            if (-not $pyProc.WaitForExit(2000)) {
                try { $pyProc.Kill() } catch { }
            }
        } elseif ($script:ServerPid -gt 0 -and (Get-Process -Id $script:ServerPid -ErrorAction SilentlyContinue)) {
            if ($script:UiToken) {
                try { Invoke-McasApi $port $script:UiToken 'POST' '/api/quit' @{} 2 | Out-Null } catch { }
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
}

function Open-Ui([int]$port, [bool]$noBrowser) {
    Get-PythonSpec | Out-Null
    $url = Get-UiUrl $port
    $owner = Get-PortOwner $port
    if ($owner -eq 'other') {
        throw "Port $port is used by another program. Use another one: mcas ui --port <n> (or set MCAS_PORT)"
    }
    if ($owner -eq 'free') {
        $oldState = Read-UiState
        if ($oldState -and ([int]$oldState.port -eq $port)) {
            Remove-Item -LiteralPath $UiState -Force -ErrorAction SilentlyContinue
        }
        Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '__serve', $port)
        $deadline = (Get-Date).AddSeconds(15)
        $ready = $false
        while ((Get-Date) -lt $deadline) {
            $st = Read-UiState
            if ($st -and [int]$st.port -eq $port -and (Get-PortOwner $port) -eq 'mcas') {
                $ready = $true
                break
            }
            Start-Sleep -Milliseconds 200
        }
        if (-not $ready) {
            throw "The mcas UI server did not start on port $port."
        }
    }
    if (-not $noBrowser) { Start-Process $url }
    return "mcas web UI: $url  (tray icon: right-click > Quit, or mcas ui stop)"
}

function Stop-Ui {
    $s = Read-UiState
    if (-not $s) { return 'The web UI is not running.' }

    $port = if ($s.port) { [int]$s.port } else { 0 }
    $serverPid = if ($s.pid) { [int]$s.pid } else { 0 }
    $token = if ($s.token) { [string]$s.token } else { '' }

    if ($port -gt 0 -and $token) {
        try {
            Invoke-McasApi $port $token 'POST' '/api/quit' @{} 3 | Out-Null
        } catch {
            if ($serverPid -gt 0) {
                Stop-Process -Id $serverPid -Force -ErrorAction SilentlyContinue
            }
        }
    } elseif ($serverPid -gt 0) {
        Stop-Process -Id $serverPid -Force -ErrorAction SilentlyContinue
    }

    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline) {
        $pidAlive = ($serverPid -gt 0) -and ((Get-Process -Id $serverPid -ErrorAction SilentlyContinue) -ne $null)
        $portBusy = ($port -gt 0) -and ((Get-PortOwner $port) -ne 'free')
        if (-not $pidAlive -and -not $portBusy) { break }
        Start-Sleep -Milliseconds 150
    }
    if ($serverPid -gt 0 -and (Get-Process -Id $serverPid -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $serverPid -Force -ErrorAction SilentlyContinue
    }

    Remove-Item -LiteralPath $UiState -Force -ErrorAction SilentlyContinue
    return 'Web UI stopped.'
}

# ---------------------------------------------------------------- Uninstall

function Invoke-Uninstall([bool]$purge) {
    Stop-Ui | Out-Null

    $binNorm = (Join-Path $Root 'bin').TrimEnd('\')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $kept = @($userPath -split ';' | Where-Object { $_ -and ($_.TrimEnd('\') -ine $binNorm) })
    [Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User')

    Remove-Item -LiteralPath $StartMenuLnk -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $StartupLnk -Force -ErrorAction SilentlyContinue

    $syncState = Join-Path $Root 'sync_state.json'
    $cmds = @()
    if ($purge -or -not (Test-Path -LiteralPath $syncState)) {
        $cmds += "rmdir /s /q `"$Root`""
    } else {
        Write-Host "Kept auto-sync settings/state in $syncState. Re-run 'mcas uninstall --purge' to delete everything."
        $cmds += "rmdir /s /q `"$(Join-Path $Root 'app')`""
        $cmds += "rmdir /s /q `"$(Join-Path $Root 'bin')`""
        $cmds += "del /f /q `"$UiState`" 2>nul"
    }
    # This script and its .cmd shim are still executing, so delete files after we exit.
    $joined = $cmds -join ' & '
    Start-Process cmd.exe -WindowStyle Hidden -ArgumentList "/c ping -n 3 127.0.0.1 >nul & $joined"
    return 'mcas uninstalled.'
}

# ---------------------------------------------------------------- CLI

function Show-Help {
    Write-Output @"
mcas $Version - Manage Coding Agent Session (Antigravity CLI + Claude Code CLI)

  mcas                                    open the web UI and start the tray icon
  mcas ui [--port <n>] [--no-browser]     start/open the web UI (default port: $DefaultPort)
  mcas ui stop                            stop the web UI server and tray icon
  mcas list [options]                     list sessions from agy and claude
  mcas copy <id|latest> --to <provider>   copy a session between providers (agy, claude)
  mcas agy2claude <id|latest> [options]   copy an agy conversation to Claude Code
  mcas claude2agy <id|latest> [options]   copy a Claude Code session to agy
  mcas sync-dir --from <p> --to <p>       copy all top-level sessions in a workspace
  mcas inspect <id> [--source <p>]        inspect a session's metadata and turns
  mcas delete <id...> --source <p> [-y]   delete one or more sessions (+ --also-synced)
  mcas trim <id> --source <p> [-y]        lighten a conversation (keep newest half/N, purge sidecars)
  mcas clean [--dry-run] [--yes]          clean orphans, subagents, old/empty/large sessions + VACUUM
  mcas autosync <status|on|off|run>       manage or run background bidirectional Auto-Sync
  mcas autostart on|off [--port <n>]      start the tray icon (and Auto-Sync) with Windows
  mcas update                             install the latest version
  mcas uninstall [--purge] [-y]           remove mcas (--purge deletes sync_state.json too)
  mcas version                            print version
  mcas help                               show this help

Run 'mcas <subcommand> --help' for detailed flags (list, copy, sync-dir, inspect, delete, trim, clean, autosync).
Data dir: $Root
"@
}

$cmd  = if ($args.Count) { [string]$args[0] } else { '' }
$rest = @($args | Select-Object -Skip 1 | ForEach-Object { [string]$_ })
$yes  = ($rest -contains '-y') -or ($rest -contains '--yes')
$pos  = @(Get-PositionalArgs $rest @('--port'))

$pyCommands = @('list', 'copy', 'agy2claude', 'claude2agy', 'sync-dir', 'inspect', 'delete', 'trim', 'lighten', 'clean', 'autosync')

try {
    switch ($cmd) {
        ''                                      { Write-Output (Open-Ui (Get-UiPort @()) $false); break }
        { $_ -in 'help', '-h', '--help', '/?' } { Show-Help; break }
        { $_ -in 'version', '-v', '--version' } { Write-Output $Version; break }
        'ui' {
            if ($pos.Count -gt 0 -and $pos[0] -eq 'stop') {
                Write-Output (Stop-Ui)
            } else {
                Write-Output (Open-Ui (Get-UiPort $rest) ($rest -contains '--no-browser'))
            }
            break
        }
        'autostart' {
            $sub = if ($pos.Count -gt 0) { $pos[0] } else { '' }
            if ($sub -eq 'on') { Write-Output (Set-Autostart $true (Get-UiPort $rest)) }
            elseif ($sub -eq 'off') { Write-Output (Set-Autostart $false 0) }
            else { Write-Output ('autostart: ' + $(if (Test-Autostart) { 'on' } else { 'off' }) + '   (mcas autostart on|off)') }
            break
        }
        '__serve' {
            if ($pos.Count -lt 1) { throw 'Missing port for __serve.' }
            Start-UiTrayServer ([int]$pos[0])
            break
        }
        '__postinstall' {
            Get-PythonSpec | Out-Null
            break
        }
        'update' {
            if ($env:MCAS_SOURCE) {
                $installer = Get-Content -LiteralPath (Join-Path $env:MCAS_SOURCE 'install.ps1') -Raw
            } else {
                $ref = if ($env:MCAS_REF) { $env:MCAS_REF } else { 'HEAD' }
                [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
                $installer = Invoke-RestMethod "$RepoRaw/$ref/install.ps1" -UseBasicParsing
            }
            Invoke-Expression $installer
            break
        }
        'uninstall' {
            $purge = $rest -contains '--purge'
            if ($purge -and -not $yes) {
                $a = Read-Host 'Remove mcas and DELETE sync_state.json? This cannot be undone. [y/N]'
                if ($a -notmatch '^[yY]') { Write-Output 'Aborted.'; break }
            }
            Write-Output (Invoke-Uninstall $purge)
            break
        }
        { $pyCommands -contains $_ } {
            $fwd = @($args | ForEach-Object { [string]$_ })
            Invoke-McasPy $fwd
            exit $LASTEXITCODE
        }
        default {
            throw "Unknown command '$cmd'. Run 'mcas help' for usage."
        }
    }
} catch {
    Write-Output $_.Exception.Message
    exit 1
}
