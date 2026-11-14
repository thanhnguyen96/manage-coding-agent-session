# Windows install & lifecycle end-to-end test in a throwaway sandbox.
# Never touches real user sessions, real %LOCALAPPDATA%\mcas, or real User PATH.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File tests\sandbox.ps1
# Keep this file ASCII-only with CRLF line endings for Windows PowerShell 5.1.

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$repo = Split-Path $PSScriptRoot
$sb = Join-Path ([IO.Path]::GetTempPath()) ('mcas-test-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$realPath = [Environment]::GetEnvironmentVariable('Path', 'User')

$env:LOCALAPPDATA = Join-Path $sb 'Local'
$env:APPDATA = Join-Path $sb 'Roaming'
$env:USERPROFILE = Join-Path $sb 'home'
$env:MCAS_ROOT = Join-Path $sb 'Local\mcas'
$env:MCAS_PORT = '47899'
$env:MCAS_AGY_DIR = Join-Path $sb 'agy-cli'
$env:MCAS_AGY_IDE_DIR = Join-Path $sb 'agy-ide'
$env:MCAS_CLAUDE_DIR = Join-Path $sb 'claude'
$env:MCAS_SOURCE = $repo
$env:UV_TOOL_DIR = Join-Path $sb 'uv-tools'
$env:UV_TOOL_BIN_DIR = Join-Path $sb 'uv-bin'
$env:Path = "$($env:UV_TOOL_BIN_DIR);$($env:Path)"

$wsA = Join-Path $sb 'work\projA'
$wsB = Join-Path $sb 'work\projB'
New-Item -ItemType Directory -Force -Path $env:LOCALAPPDATA, $env:APPDATA, $env:USERPROFILE, $env:UV_TOOL_DIR, $env:UV_TOOL_BIN_DIR, $wsA, $wsB | Out-Null

$fail = 0
function Has($out, $pattern) { return [bool](@($out) -match $pattern).Count }
function Get-PortState($port) {
    if (@(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).Count) { 'used' } else { 'free' }
}
function Check($name, [bool]$ok) {
    if ($ok) { Write-Host "  ok   $name" } else { Write-Host "  FAIL $name" -ForegroundColor Red; $script:fail++ }
}

$pyExe = $null
$pyPre = @()
foreach ($cand in @(@{ E = 'python'; P = @() }, @{ E = 'py'; P = @('-3') })) {
    $c = Get-Command $cand.E -ErrorAction SilentlyContinue
    if (-not $c) { continue }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $null = & $c.Source @($cand.P + @('-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)')) 2>$null
        if ($LASTEXITCODE -eq 0) { $pyExe = $c.Source; $pyPre = $cand.P; break }
    } catch { } finally { $ErrorActionPreference = $prev }
}
if (-not $pyExe) { throw 'Python 3.10+ is required to run sandbox.ps1.' }

$uvCmd = Get-Command 'uv' -ErrorAction SilentlyContinue
if (-not $uvCmd) { throw 'uv is required to run sandbox.ps1.' }

$root = $env:MCAS_ROOT
$mcas = Join-Path $env:UV_TOOL_BIN_DIR 'mcas.exe'
$agySeedId = '11111111-1111-4111-8111-111111111111'

try {
    Write-Host "sandbox: $sb"

    # Seed realistic mock agy + claude sessions via tests/fixtures/seed.py
    $seedScript = Join-Path $repo 'tests\fixtures\seed.py'
    & $pyExe @($pyPre + @('-B', $seedScript, $wsA, $wsB))
    if ($LASTEXITCODE -ne 0) { throw 'Failed to seed sandbox data.' }

    # 1. Install package into sandboxed UV_TOOL_DIR / UV_TOOL_BIN_DIR
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $instOut = & $uvCmd.Source tool install --force --reinstall $repo 2>&1
    $instExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if ($instExit -ne 0) { throw "uv tool install failed ($instExit): $($instOut -join "`n")" }
    Check 'install: mcas.exe created in UV_TOOL_BIN_DIR' (Test-Path -LiteralPath $mcas)

    # 2. CLI version & list
    Check 'cli: version' (Has (& $mcas version) '^\d+\.\d+\.\d+$')

    $listJson = (& $mcas list --json) -join "`n"
    $listed = $listJson | ConvertFrom-Json
    Check 'cli: list sees all 3 seeded sessions' (@($listed).Count -eq 3)
    $agyRow = @($listed | Where-Object { $_.id -eq $agySeedId })[0]
    $vnCheck = 'B' + [char]0x00E1 + 'nh m' + [char]0x00EC
    Check 'cli: list preserves Vietnamese UTF-8' ($agyRow -and $agyRow.title -match $vnCheck)

    # Touch sync_state.json so update preservation can be verified
    $null = & $mcas autosync off

    # 3. Autostart on / off
    $startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\mcas.lnk'
    Check 'cli: autostart on' ((Has (& $mcas autostart on) 'start in the tray') -and (Test-Path -LiteralPath $startup))
    Check 'cli: autostart off' ((Has (& $mcas autostart off) 'not start') -and -not (Test-Path -LiteralPath $startup))

    # 4. Web UI start + /health + token + API auth
    $uiStartOut = & $mcas ui --no-browser
    if ($LASTEXITCODE -ne 0) { throw "mcas ui --no-browser failed: $uiStartOut" }
    $u = "http://127.0.0.1:$($env:MCAS_PORT)"
    $health = Invoke-RestMethod "$u/health" -UseBasicParsing
    Check 'ui: /health ok' ($health.app -eq 'mcas')

    $page = (Invoke-WebRequest "$u/" -UseBasicParsing).Content
    $tok = [regex]::Match($page, "const token = '([0-9a-f]{32})'").Groups[1].Value
    Check 'ui: token extracted from /' ($tok.Length -eq 32)
    $h = @{ 'X-Mcas-Token' = $tok }

    $code = try { Invoke-WebRequest "$u/api/status" -UseBasicParsing | Out-Null; 200 } catch { [int]$_.Exception.Response.StatusCode }
    Check 'ui: no token -> 403' ($code -eq 403)

    $st = Invoke-RestMethod "$u/api/status" -Headers $h -UseBasicParsing
    Check 'ui: /api/status with token ok' (@($st.providers).Count -ge 2)

    $uiPid = [int](Get-Content -LiteralPath (Join-Path $root 'ui.json') -Raw | ConvertFrom-Json).pid
    Check 'ui: server stays up' ((Get-Process -Id $uiPid -ErrorAction SilentlyContinue) -ne $null)

    # 5. Update while UI server is running
    $updOut = & $mcas update
    Check 'update: installs and keeps sync_state' ((Has $updOut 'installed|updated') -and (Test-Path -LiteralPath (Join-Path $root 'sync_state.json')))
    $healthAfterUpd = Invoke-RestMethod "$u/health" -UseBasicParsing
    Check 'update: restarts running UI server' ($healthAfterUpd.app -eq 'mcas')

    # 6. UI stop
    $stopOut = & $mcas ui stop
    Check 'ui stop: prints stopped & frees port' ((Has $stopOut 'stopped') -and ((Get-PortState $env:MCAS_PORT) -eq 'free'))

    # 7. Uninstall --purge -y
    $uninstOut = & $mcas uninstall --purge -y
    Check 'uninstall: reports uninstalled' (Has $uninstOut 'uninstalled')
    $uninstDeadline = (Get-Date).AddSeconds(10)
    while (((Test-Path -LiteralPath $root) -or (Test-Path -LiteralPath $mcas)) -and (Get-Date) -lt $uninstDeadline) {
        Start-Sleep -Milliseconds 250
    }
    Check 'uninstall: mcas root folder removed' (-not (Test-Path -LiteralPath $root))
    Check 'uninstall: mcas.exe tool removed' (-not (Test-Path -LiteralPath $mcas))
    Check 'uninstall: start menu shortcut removed' (-not (Test-Path -LiteralPath (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\mcas.lnk')))
} finally {
    try {
        $uiJson = Join-Path $root 'ui.json'
        if (Test-Path -LiteralPath $mcas) { & $mcas ui stop 2>$null | Out-Null }
        if (Test-Path -LiteralPath $uiJson) {
            $stCleanup = Get-Content -LiteralPath $uiJson -Raw | ConvertFrom-Json
            if ($stCleanup.pid) { Stop-Process -Id ([int]$stCleanup.pid) -Force -ErrorAction SilentlyContinue }
        }
    } catch { }
    $p = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($p -ne $realPath) {
        [Environment]::SetEnvironmentVariable('Path', $realPath, 'User')
    }
    $finalPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    Check 'user PATH unchanged' ($finalPath -eq $realPath)
    cmd /c rmdir /s /q "`"$sb`"" 2>$null
}

if ($fail) { Write-Host "$fail check(s) failed" -ForegroundColor Red; exit 1 }
Write-Host 'all checks passed' -ForegroundColor Green
