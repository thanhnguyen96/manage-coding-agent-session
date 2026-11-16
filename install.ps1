# mcas (Manage Coding Agent Session) installer (uv-managed).
#   irm https://raw.githubusercontent.com/thanhnguyen96/manage-coding-agent-session/HEAD/install.ps1 | iex
# Options (environment variables):
#   MCAS_REF     git ref to install (tag, branch or commit). Default: HEAD
#   MCAS_SOURCE  install from a local folder instead of GitHub (for development)
#   MCAS_ROOT    data directory (ui.json, sync_state.json). Default: %LOCALAPPDATA%\mcas
#   MCAS_PORT    default web UI port. Default: 47831
# Installs per user via uv tool install. No admin rights needed.
# Keep this file ASCII-only with CRLF line endings (Windows PowerShell 5.1 reads BOM-less files as ANSI).

& {
    $ErrorActionPreference = 'Stop'
    $repoGit = 'git+https://github.com/thanhnguyen96/manage-coding-agent-session'
    $ref     = if ($env:MCAS_REF) { $env:MCAS_REF } else { 'HEAD' }
    $root    = if ($env:MCAS_ROOT) { $env:MCAS_ROOT } else { Join-Path $env:LOCALAPPDATA 'mcas' }

    if ($env:OS -ne 'Windows_NT') {
        throw "mcas install.ps1 is for Windows. On macOS/Linux, run: uv tool install $repoGit@$ref"
    }

    function Find-UvExe {
        $cmd = Get-Command uv -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
        foreach ($cand in @(
            (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
            (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe'),
            (Join-Path $env:LOCALAPPDATA 'uv\uv.exe')
        )) {
            if ($cand -and (Test-Path -LiteralPath $cand)) { return $cand }
        }
        return $null
    }

    $uvExe = Find-UvExe
    if (-not $uvExe) {
        Write-Host 'uv is not installed. Installing uv via official installer (or install manually: winget install astral-sh.uv)...'
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
            $uvInstaller = Invoke-RestMethod 'https://astral.sh/uv/install.ps1' -UseBasicParsing
            Invoke-Expression $uvInstaller
        } catch {
            throw "uv is required. Install uv with 'winget install astral-sh.uv' or 'irm https://astral.sh/uv/install.ps1 | iex', then re-run this installer."
        }
        $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
        $userPathNow = [Environment]::GetEnvironmentVariable('Path', 'User')
        $env:Path = (@($userPathNow, $machinePath, $env:Path) | Where-Object { $_ }) -join ';'
        $uvExe = Find-UvExe
        if (-not $uvExe) {
            throw "uv was installed but not found on PATH. Open a new terminal or install with 'winget install astral-sh.uv', then re-run."
        }
    }

    # Stop a running UI server / tray icon before replacing the tool; restart it below.
    $uiWasRunning = $false
    $oldPort = 0
    $uiStatePath = Join-Path $root 'ui.json'
    if (Test-Path -LiteralPath $uiStatePath) {
        try {
            $st = Get-Content -LiteralPath $uiStatePath -Raw | ConvertFrom-Json
            if ($st.port) { $oldPort = [int]$st.port }
            $srvPid = if ($st.pid) { [int]$st.pid } else { 0 }
            $tok = if ($st.token) { [string]$st.token } else { '' }
            if ($srvPid -gt 0 -and (Get-Process -Id $srvPid -ErrorAction SilentlyContinue)) {
                $uiWasRunning = $true
            }
            if ($oldPort -gt 0 -and $tok) {
                try {
                    Invoke-RestMethod -Uri "http://127.0.0.1:$oldPort/api/quit" -Method Post `
                        -Headers @{ 'X-Mcas-Token' = $tok } -Body '{}' `
                        -ContentType 'application/json; charset=utf-8' -TimeoutSec 3 -UseBasicParsing | Out-Null
                } catch { }
            }
            $deadline = (Get-Date).AddSeconds(4)
            while ($srvPid -gt 0 -and (Get-Date) -lt $deadline) {
                if (-not (Get-Process -Id $srvPid -ErrorAction SilentlyContinue)) { break }
                Start-Sleep -Milliseconds 150
            }
            if ($srvPid -gt 0 -and (Get-Process -Id $srvPid -ErrorAction SilentlyContinue)) {
                Stop-Process -Id $srvPid -Force -ErrorAction SilentlyContinue
            }
        } catch { }
        Remove-Item -LiteralPath $uiStatePath -Force -ErrorAction SilentlyContinue
    }

    # Migration: remove legacy %LOCALAPPDATA%\mcas\bin from User PATH + old app\ / bin\ folders.
    $oldBinNorm = (Join-Path $root 'bin').TrimEnd('\')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($userPath) {
        $parts = @($userPath -split ';' | Where-Object { $_ })
        $kept = @($parts | Where-Object { $_.TrimEnd('\') -ine $oldBinNorm })
        if ($kept.Count -ne $parts.Count) {
            [Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User')
            Write-Host "Removed legacy $oldBinNorm from User PATH."
        }
    }
    $envParts = @($env:Path -split ';' | Where-Object { $_ -and ($_.TrimEnd('\') -ine $oldBinNorm) })
    $env:Path = $envParts -join ';'
    Remove-Item -LiteralPath (Join-Path $root 'app') -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $root 'bin') -Recurse -Force -ErrorAction SilentlyContinue

    # Install or reinstall mcas as a uv tool.
    $src = if ($env:MCAS_SOURCE) {
        Write-Host "Installing mcas from $env:MCAS_SOURCE ..."
        $env:MCAS_SOURCE
    } else {
        Write-Host "Installing mcas ($ref) ..."
        "$repoGit@$ref"
    }

    & $uvExe tool install --force --reinstall $src
    if ($LASTEXITCODE -ne 0) {
        throw "uv tool install failed with exit code $LASTEXITCODE."
    }

    $toolBinDir = (& $uvExe tool dir --bin | Select-Object -Last 1).Trim()
    $toolDir = (& $uvExe tool dir | Select-Object -Last 1).Trim()
    if (-not $toolBinDir) {
        $toolBinDir = Join-Path $env:USERPROFILE '.local\bin'
    }

    # Ensure tool bin dir is on current PATH (and User PATH for normal non-sandbox installs).
    $binNorm = $toolBinDir.TrimEnd('\')
    if (-not $env:UV_TOOL_BIN_DIR) {
        $curUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        $uParts = @($curUserPath -split ';' | Where-Object { $_ })
        if (-not ($uParts | Where-Object { $_.TrimEnd('\') -ieq $binNorm })) {
            [Environment]::SetEnvironmentVariable('Path', (($uParts + $toolBinDir) -join ';'), 'User')
            Write-Host "Added $toolBinDir to your PATH."
        }
    }
    if (-not (($env:Path -split ';') | Where-Object { $_.TrimEnd('\') -ieq $binNorm })) {
        $env:Path = "$toolBinDir;$env:Path"
    }

    $mcasExe = Join-Path $toolBinDir 'mcas.exe'
    if (-not (Test-Path -LiteralPath $mcasExe)) {
        throw "Expected installed binary not found: $mcasExe"
    }

    # Start menu entry that opens the web UI without a console flash.
    $lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\mcas.lnk'
    New-Item -ItemType Directory -Force -Path (Split-Path $lnk) | Out-Null
    $pywExe = if ($toolDir) { Join-Path $toolDir 'mcas\Scripts\pythonw.exe' } else { '' }
    $sh = New-Object -ComObject WScript.Shell
    $s = $sh.CreateShortcut($lnk)
    if ($pywExe -and (Test-Path -LiteralPath $pywExe)) {
        $s.TargetPath = $pywExe
        $s.Arguments = '-m mcas ui'
    } else {
        $s.TargetPath = 'powershell.exe'
        $s.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"& '$mcasExe' ui`""
    }
    $s.Description = 'Manage Coding Agent Session (mcas)'
    $s.WindowStyle = 7
    $s.Save()

    $v = (& $mcasExe version | Select-Object -Last 1).Trim()
    $port = if ($oldPort -gt 0) { [string]$oldPort } elseif ($env:MCAS_PORT) { $env:MCAS_PORT } else { '47831' }
    if ($uiWasRunning) {
        if ($oldPort -gt 0) {
            & $mcasExe ui --no-browser --port $oldPort | Out-Null
        } else {
            & $mcasExe ui --no-browser | Out-Null
        }
    }

    Write-Host ''
    Write-Host "mcas $v installed." -ForegroundColor Green
    Write-Host ''
    Write-Host '  Web UI'
    Write-Host "    mcas                                  start server + tray icon and open http://127.0.0.1:$port"
    Write-Host '    mcas ui --no-browser                  start server + tray icon without opening a browser'
    Write-Host '    mcas ui stop                          stop the web UI server and tray icon'
    Write-Host '    or Start menu > mcas'
    Write-Host '    mcas autostart on                     start the tray icon (and Auto-Sync) with Windows'
    Write-Host ''
    Write-Host '  Terminal'
    Write-Host '    mcas list                             list sessions from agy, claude, and codex'
    Write-Host '    mcas copy <id> --to claude|agy|codex  copy a session between agents'
    Write-Host '    mcas sync-dir --from agy --to claude  sync all sessions for a workspace'
    Write-Host '    mcas autosync status                  check or configure background Auto-Sync'
    Write-Host '    mcas help                             all commands'
    Write-Host ''
    Write-Host '  Open a new terminal first so PATH picks up mcas.'
}
