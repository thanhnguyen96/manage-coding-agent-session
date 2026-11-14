# mcas (Manage Coding Agent Session) installer.
#   irm https://raw.githubusercontent.com/thanhnguyen96/manage-coding-agent-session/HEAD/install.ps1 | iex
# Options (environment variables):
#   MCAS_REF     git ref to install (tag, branch or commit). Default: HEAD
#   MCAS_SOURCE  install from a local folder instead of GitHub (for development)
#   MCAS_ROOT    install directory. Default: %LOCALAPPDATA%\mcas
#   MCAS_PORT    default web UI port. Default: 47831
# Installs per user into %LOCALAPPDATA%\mcas. No admin rights needed.
# Keep this file ASCII-only (Windows PowerShell 5.1 reads BOM-less files as ANSI).

& {
    $ErrorActionPreference = 'Stop'
    $repoRaw = 'https://raw.githubusercontent.com/thanhnguyen96/manage-coding-agent-session'
    $ref     = if ($env:MCAS_REF) { $env:MCAS_REF } else { 'HEAD' }
    $root    = if ($env:MCAS_ROOT) { $env:MCAS_ROOT } else { Join-Path $env:LOCALAPPDATA 'mcas' }
    $app     = Join-Path $root 'app'
    $bin     = Join-Path $root 'bin'
    $files   = @('mcas.ps1', 'mcas.py', 'ui.html')

    if ($env:OS -ne 'Windows_NT') {
        throw 'mcas installer is for Windows. On macOS/Linux, run mcas.py directly with Python 3.'
    }

    # Verify Python 3 (python or py -3) is installed and working.
    $pyFound = $false
    foreach ($cand in @(
        @{ Exe = 'python'; Prefix = @() },
        @{ Exe = 'py';     Prefix = @('-3') }
    )) {
        $cmd = Get-Command $cand.Exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $checkArgs = @($cand.Prefix) + @('-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)')
            $null = & $cmd.Source @checkArgs 2>$null
            if ($LASTEXITCODE -eq 0) { $pyFound = $true; break }
        } catch {
        } finally {
            $ErrorActionPreference = $prevEap
        }
    }
    if (-not $pyFound) {
        throw 'Python 3 is required. Install Python 3 (https://www.python.org or "winget install Python.Python.3.12") and ensure "python" or "py" is on PATH.'
    }

    # Download or stage files to a temp folder first so a failure never leaves a broken install.
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('mcas-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $uiWasRunning = $false
    $oldPort = 0
    try {
        if ($env:MCAS_SOURCE) {
            Write-Host "Installing mcas from $env:MCAS_SOURCE ..."
            foreach ($f in $files) {
                $src = Join-Path $env:MCAS_SOURCE $f
                if (-not (Test-Path -LiteralPath $src)) { throw "Missing source file: $src" }
                Copy-Item -LiteralPath $src -Destination (Join-Path $tmp $f)
            }
        } else {
            Write-Host "Installing mcas ($ref) ..."
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
            foreach ($f in $files) {
                Invoke-WebRequest -Uri "$repoRaw/$ref/$f" -OutFile (Join-Path $tmp $f) -UseBasicParsing
            }
        }

        New-Item -ItemType Directory -Force -Path $app, $bin | Out-Null

        # Stop a running UI server / tray icon before replacing files; restart it below.
        $uiStatePath = Join-Path $root 'ui.json'
        $installedPs1 = Join-Path $app 'mcas.ps1'
        if ((Test-Path -LiteralPath $uiStatePath) -and (Test-Path -LiteralPath $installedPs1)) {
            try {
                $st = Get-Content -LiteralPath $uiStatePath -Raw | ConvertFrom-Json
                if ($st.port) { $oldPort = [int]$st.port }
                if ($st.pid -and (Get-Process -Id ([int]$st.pid) -ErrorAction SilentlyContinue)) {
                    $uiWasRunning = $true
                }
            } catch { }
            & powershell -NoProfile -ExecutionPolicy Bypass -File $installedPs1 ui stop | Out-Null
        }

        Remove-Item -LiteralPath (Join-Path $app '__pycache__') -Recurse -Force -ErrorAction SilentlyContinue
        foreach ($f in $files) {
            Copy-Item -LiteralPath (Join-Path $tmp $f) -Destination (Join-Path $app $f) -Force
        }
    } finally {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }

    $shim = "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0..\app\mcas.ps1`" %*`r`n"
    [IO.File]::WriteAllText((Join-Path $bin 'mcas.cmd'), $shim, [Text.Encoding]::ASCII)
    [IO.File]::WriteAllText((Join-Path $bin 'manage-coding-agent-session.cmd'), $shim, [Text.Encoding]::ASCII)

    # Add bin to the user PATH (and to this session).
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @($userPath -split ';' | Where-Object { $_ })
    $binNorm = $bin.TrimEnd('\')
    if (-not ($parts | Where-Object { $_.TrimEnd('\') -ieq $binNorm })) {
        [Environment]::SetEnvironmentVariable('Path', (($parts + $bin) -join ';'), 'User')
        Write-Host "Added $bin to your PATH."
    }
    if (-not (($env:Path -split ';') | Where-Object { $_.TrimEnd('\') -ieq $binNorm })) {
        $env:Path = "$env:Path;$bin"
    }

    # Start menu entry that opens the web UI.
    $lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\mcas.lnk'
    New-Item -ItemType Directory -Force -Path (Split-Path $lnk) | Out-Null
    $sh = New-Object -ComObject WScript.Shell
    $s = $sh.CreateShortcut($lnk)
    $s.TargetPath = 'powershell.exe'
    $s.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$app\mcas.ps1`" ui"
    $s.Description = 'Manage Coding Agent Session (mcas)'
    $s.WindowStyle = 7
    $s.Save()

    $main = Join-Path $app 'mcas.ps1'
    & powershell -NoProfile -ExecutionPolicy Bypass -File $main __postinstall
    $v = & powershell -NoProfile -ExecutionPolicy Bypass -File $main version
    $port = if ($oldPort -gt 0) { [string]$oldPort } elseif ($env:MCAS_PORT) { $env:MCAS_PORT } else { '47831' }
    if ($uiWasRunning) {
        if ($oldPort -gt 0) {
            & powershell -NoProfile -ExecutionPolicy Bypass -File $main ui --no-browser --port $oldPort | Out-Null
        } else {
            & powershell -NoProfile -ExecutionPolicy Bypass -File $main ui --no-browser | Out-Null
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
    Write-Host '    mcas list                             list sessions from agy and claude'
    Write-Host '    mcas copy <id> --to claude|agy        copy a session between agents'
    Write-Host '    mcas sync-dir --from agy --to claude  sync all sessions for a workspace'
    Write-Host '    mcas autosync status                  check or configure background Auto-Sync'
    Write-Host '    mcas help                             all commands'
    Write-Host ''
    Write-Host '  Open a new terminal first so PATH picks up mcas.'
}
