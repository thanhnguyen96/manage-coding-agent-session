# End-to-end test in a throwaway sandbox. Never touches your real sessions or User PATH:
# LOCALAPPDATA, APPDATA, USERPROFILE, MCAS_ROOT, MCAS_AGY_DIR, MCAS_CLAUDE_DIR are
# redirected to a temp folder, and User PATH is restored in finally.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File tests\sandbox.ps1
# Keep this file ASCII-only (Windows PowerShell 5.1 reads BOM-less files as ANSI).

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

$wsA = Join-Path $sb 'work\projA'
$wsB = Join-Path $sb 'work\projB'
$wsC = Join-Path $sb 'work\projAuto'
New-Item -ItemType Directory -Force -Path $env:LOCALAPPDATA, $env:APPDATA, $env:USERPROFILE, $wsA, $wsB, $wsC | Out-Null

$fail = 0
function Has($out, $pattern) { return [bool](@($out) -match $pattern).Count }
function Get-PortState($port) {
    if (@(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).Count) { 'used' } else { 'free' }
}
function Check($name, [bool]$ok) {
    if ($ok) { Write-Host "  ok   $name" } else { Write-Host "  FAIL $name" -ForegroundColor Red; $script:fail++ }
}
function Invoke-McasJsonPost($url, $headers, $obj) {
    $json = ConvertTo-Json -InputObject $obj -Depth 8 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    return Invoke-RestMethod -Uri $url -Method Post -Headers $headers -Body $bytes -ContentType 'application/json; charset=utf-8' -UseBasicParsing
}
function Get-ClaudeSlug([string]$path) {
    $full = [IO.Path]::GetFullPath($path).TrimEnd('\')
    if ($full.Length -eq 2 -and $full[1] -eq ':') { $full += '\' }
    if ($full.Length -ge 2 -and $full[1] -eq ':') {
        $full = $full.Substring(0, 1).ToUpperInvariant() + $full.Substring(1)
    }
    return ([regex]::Replace($full, '[^a-zA-Z0-9]', '-'))
}

# Resolve Python 3 for seeding realistic mock agy/claude sessions.
$pyExe = $null
$pyPre = @()
foreach ($cand in @(@{ E = 'python'; P = @() }, @{ E = 'py'; P = @('-3') })) {
    $c = Get-Command $cand.E -ErrorAction SilentlyContinue
    if (-not $c) { continue }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $null = & $c.Source @($cand.P + @('-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)')) 2>$null
        if ($LASTEXITCODE -eq 0) { $pyExe = $c.Source; $pyPre = $cand.P; break }
    } catch { } finally { $ErrorActionPreference = $prev }
}
if (-not $pyExe) { throw 'Python 3.8+ is required to run sandbox.ps1.' }

$agySeedId = '11111111-1111-4111-8111-111111111111'
$claudeSeedId = '22222222-2222-4222-8222-222222222222'
$claudeSeedBId = '33333333-3333-4333-8333-333333333333'
$agyAutoId = '44444444-4444-4444-8444-444444444444'

# Seed realistic mock agy SQLite DB + conversation_summaries.db + Claude JSONL files.
$seedScript = @'
import json, os, re, sqlite3, sys
from pathlib import Path

agy_dir = Path(os.environ["MCAS_AGY_DIR"])
claude_dir = Path(os.environ["MCAS_CLAUDE_DIR"])

def enc_varint(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)

def pb_varint(fn, v):
    return enc_varint((fn << 3) | 0) + enc_varint(v)

def pb_bytes(fn, data):
    return enc_varint((fn << 3) | 2) + enc_varint(len(data)) + data

def pb_str(fn, s):
    return pb_bytes(fn, s.encode("utf-8"))

def pb_ts(fn, sec):
    return pb_bytes(fn, pb_varint(1, sec) + pb_varint(2, 0))

def win_uri(p):
    return Path(os.path.abspath(p)).as_uri()

def slug(p):
    norm = os.path.normpath(os.path.abspath(p))
    if len(norm) >= 2 and norm[1] == ":" and norm[0].isalpha():
        norm = norm[0].upper() + norm[1:]
    return re.sub(r"[^a-zA-Z0-9]", "-", norm)

def make_agy_db(conv_id, ws_path, title, user_text, thought_text, reply_text, sec):
    conv_dir = agy_dir / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    db_path = conv_dir / f"{conv_id}.db"
    traj_id = "traj-" + conv_id[:8]
    uri = win_uri(ws_path)
    traj_meta = pb_bytes(1, pb_str(1, uri)) + pb_ts(2, sec) + pb_str(7, uri) + pb_varint(17, 0)

    m0 = pb_ts(1, sec) + pb_varint(2, 4) + pb_str(5, traj_id) + pb_varint(6, 0) + pb_str(7, conv_id)
    ui = pb_str(2, user_text) + pb_bytes(3, pb_str(1, user_text))
    s0 = pb_varint(1, 14) + pb_varint(4, 3) + pb_bytes(5, m0) + pb_bytes(19, ui)

    m1 = pb_ts(1, sec + 2) + pb_varint(2, 2) + pb_str(5, traj_id) + pb_varint(6, 1) + pb_str(7, conv_id)
    tc = pb_str(1, "call_seed_1") + pb_str(2, "read_file") + pb_str(3, '{"path":"README.md"}')
    pr1 = pb_str(3, thought_text) + pb_str(6, "bot-1") + pb_bytes(7, tc) + pb_varint(12, 2)
    s1 = pb_varint(1, 15) + pb_varint(4, 3) + pb_bytes(5, m1) + pb_bytes(20, pr1)

    m2 = pb_ts(1, sec + 3) + pb_varint(2, 5) + pb_bytes(4, tc) + pb_str(5, traj_id) + pb_varint(6, 2) + pb_str(7, conv_id)
    gen = pb_bytes(2, pb_str(1, "README content ok"))
    s2 = pb_varint(1, 132) + pb_varint(4, 3) + pb_bytes(5, m2) + pb_bytes(140, gen)

    m3 = pb_ts(1, sec + 5) + pb_varint(2, 2) + pb_str(5, traj_id) + pb_varint(6, 3) + pb_str(7, conv_id)
    pr2 = pb_str(6, "bot-2") + pb_str(8, reply_text) + pb_varint(12, 2)
    s3 = pb_varint(1, 15) + pb_varint(4, 3) + pb_bytes(5, m3) + pb_bytes(20, pr2)

    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE IF NOT EXISTS trajectory_meta (trajectory_id text PRIMARY KEY, cascade_id text, trajectory_type integer, source integer);
        CREATE TABLE IF NOT EXISTS steps (idx integer PRIMARY KEY, step_type integer NOT NULL DEFAULT 0, status integer NOT NULL DEFAULT 0, has_subtrajectory numeric NOT NULL DEFAULT false, metadata blob, error_details blob, permissions blob, task_details blob, render_info blob, step_payload blob, step_format integer NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS trajectory_metadata_blob (id text PRIMARY KEY, data blob);
    """)
    con.execute("INSERT INTO trajectory_meta VALUES (?, ?, 4, 17)", (traj_id, conv_id))
    con.execute("INSERT INTO trajectory_metadata_blob VALUES ('main', ?)", (traj_meta,))
    con.executemany(
        "INSERT INTO steps (idx, step_type, status, has_subtrajectory, metadata, step_payload, step_format) VALUES (?, ?, 3, 0, ?, ?, 0)",
        [(0, 14, m0, s0), (1, 15, m1, s1), (2, 132, m2, s2), (3, 15, m3, s3)],
    )
    con.commit()
    con.close()

    sum_db = agy_dir / "conversation_summaries.db"
    sc = sqlite3.connect(str(sum_db))
    sc.executescript("""
        CREATE TABLE IF NOT EXISTS conversation_summaries (
            conversation_id text PRIMARY KEY, title text NOT NULL DEFAULT "", preview text NOT NULL DEFAULT "",
            step_count integer NOT NULL DEFAULT 0, last_modified_time datetime NOT NULL, workspace_uris text NOT NULL,
            status text NOT NULL DEFAULT "", source text NOT NULL DEFAULT "", project_id text NOT NULL DEFAULT "",
            agent_name text NOT NULL DEFAULT "", parent_conversation_id text NOT NULL DEFAULT "", nesting_depth integer NOT NULL DEFAULT 0,
            battle_id text NOT NULL DEFAULT "", winning_conversation_id text NOT NULL DEFAULT "",
            not_fully_idle numeric NOT NULL DEFAULT false, killed numeric NOT NULL DEFAULT false,
            last_user_input_time datetime NOT NULL, last_user_input_step_index integer NOT NULL DEFAULT -1,
            app_data_dir text NOT NULL DEFAULT "", raw_summary blob, group_id TEXT NOT NULL DEFAULT ''
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
            conversation_id UNINDEXED, chunk_index UNINDEXED, chunk_first_step UNINDEXED, chunk_last_step UNINDEXED,
            last_modified_time UNINDEXED, step_count UNINDEXED, last_step_created_at UNINDEXED, archived UNINDEXED,
            user_step_byte_offsets UNINDEXED, agent_step_byte_offsets UNINDEXED, title, workspace, project, user_body, agent_body
        );
    """)
    ts_str = "2026-11-14 09:00:00.000000+00:00"
    sc.execute(
        "INSERT OR REPLACE INTO conversation_summaries VALUES (?, ?, ?, 4, ?, ?, 'CASCADE_RUN_STATUS_IDLE', '', 'default-cli-project', '', '', 0, '', '', 0, 0, ?, 0, 'antigravity-cli', ?, '')",
        (conv_id, title, user_text[:80], ts_str, json.dumps([uri]), ts_str, pb_str(1, user_text[:80]) + pb_varint(2, 4)),
    )
    sc.commit()
    sc.close()

def make_claude_jsonl(sid, ws_path, user_text, reply_text, ts_iso):
    pdir = claude_dir / "projects" / slug(ws_path)
    pdir.mkdir(parents=True, exist_ok=True)
    jpath = pdir / f"{sid}.jsonl"
    recs = [
        {"type": "user", "uuid": "u-" + sid[:8], "parentUuid": None, "sessionId": sid, "isSidechain": False, "timestamp": ts_iso, "cwd": ws_path, "message": {"role": "user", "content": user_text}},
        {"type": "assistant", "uuid": "a1-" + sid[:8], "parentUuid": "u-" + sid[:8], "sessionId": sid, "isSidechain": False, "timestamp": ts_iso, "cwd": ws_path, "message": {"id": "msg_1", "role": "assistant", "content": [{"type": "thinking", "thinking": "Checking tools"}, {"type": "tool_use", "id": "toolu_seed_1", "name": "Read", "input": {"file_path": "app.py"}}]}},
        {"type": "user", "uuid": "u2-" + sid[:8], "parentUuid": "a1-" + sid[:8], "sessionId": sid, "isSidechain": False, "timestamp": ts_iso, "cwd": ws_path, "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_seed_1", "content": "print('ok')"}]}},
        {"type": "assistant", "uuid": "a2-" + sid[:8], "parentUuid": "u2-" + sid[:8], "sessionId": sid, "isSidechain": False, "timestamp": ts_iso, "cwd": ws_path, "message": {"id": "msg_2", "role": "assistant", "content": [{"type": "text", "text": reply_text}]}},
    ]
    jpath.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")

if __name__ == "__main__":
    ws_a, ws_b, ws_c = sys.argv[1], sys.argv[2], sys.argv[3]
    make_agy_db(
        "11111111-1111-4111-8111-111111111111",
        ws_a,
        "AGY Seed: B\u00e1nh m\u00ec H\u00e0 N\u1ed9i 2026",
        "Xin ch\u00e0o B\u00e1nh m\u00ec H\u00e0 N\u1ed9i 2026 from AGY",
        "Internal reasoning trace",
        "Ack AGY response TOKEN_AGY_8811",
        1763110800,
    )
    make_claude_jsonl(
        "22222222-2222-4222-8222-222222222222",
        ws_a,
        "C\u00e0 ph\u00ea s\u1eefa \u0111\u00e1 2026 from Claude",
        "Claude reply TOKEN_CLAUDE_9922",
        "2026-11-14T09:10:00.000Z",
    )
    make_claude_jsonl(
        "33333333-3333-4333-8333-333333333333",
        ws_b,
        "Workspace B Claude session",
        "Workspace B reply TOKEN_WSB_3344",
        "2026-11-14T09:20:00.000Z",
    )
'@
$seedPath = Join-Path $sb 'seed.py'
[IO.File]::WriteAllText($seedPath, $seedScript, [Text.Encoding]::UTF8)
& $pyExe @($pyPre + @('-B', $seedPath, $wsA, $wsB, $wsC))
if ($LASTEXITCODE -ne 0) { throw 'Failed to seed sandbox data.' }

$mcas = Join-Path $env:MCAS_ROOT 'bin\mcas.cmd'
$mcasLong = Join-Path $env:MCAS_ROOT 'bin\manage-coding-agent-session.cmd'
$root = $env:MCAS_ROOT

try {
    Write-Host "sandbox: $sb"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'install.ps1') | Out-Null
    Check 'install: mcas.cmd created' (Test-Path -LiteralPath $mcas)
    Check 'install: long shim created' (Test-Path -LiteralPath $mcasLong)
    Check 'install: mcas.py & ui.html copied' ((Test-Path -LiteralPath (Join-Path $root 'app\mcas.py')) -and (Test-Path -LiteralPath (Join-Path $root 'app\ui.html')))
    Check 'install: start menu shortcut' (Test-Path -LiteralPath (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\mcas.lnk'))
    Check 'cli: version' (Has (& $mcas version) '^\d+\.\d+\.\d+$')

    # CLI tests
    $listJson = (& $mcas list --json) -join "`n"
    $listed = $listJson | ConvertFrom-Json
    Check 'cli: list sees all 3 seeded sessions' (@($listed).Count -eq 3)
    $agyRow = @($listed | Where-Object { $_.id -eq $agySeedId })[0]
    $vnCheck = "B" + [char]0x00E1 + "nh m" + [char]0x00EC
    Check 'cli: list preserves Vietnamese UTF-8' ($agyRow -and $agyRow.title -match $vnCheck)

    $inspJson = (& $mcas inspect $agySeedId --source agy --json) -join "`n"
    $insp = $inspJson | ConvertFrom-Json
    Check 'cli: inspect returns turns' (@($insp.turns).Count -ge 3)

    $c2aOut = (& $mcas claude2agy $claudeSeedId --new-id --json) -join "`n"
    $c2aObj = $c2aOut | ConvertFrom-Json
    Check 'cli: claude2agy creates agy db' ($c2aObj.ok -and (Test-Path -LiteralPath $c2aObj.path))

    $a2cOut = (& $mcas agy2claude $agySeedId --new-id --include-thinking --json) -join "`n"
    $a2cObj = $a2cOut | ConvertFrom-Json
    Check 'cli: agy2claude creates claude jsonl' ($a2cObj.ok -and (Test-Path -LiteralPath $a2cObj.path))
    $a2cRaw = [IO.File]::ReadAllText($a2cObj.path, [Text.Encoding]::UTF8)
    Check 'cli: agy2claude --include-thinking includes thought tag' ($a2cRaw -match '<thought>')

    $copyOut = (& $mcas copy $agySeedId --to claude --new-id --json) -join "`n"
    $copyObj = $copyOut | ConvertFrom-Json
    Check 'cli: copy --to claude works' ($copyObj.ok -and (Test-Path -LiteralPath $copyObj.path))

    $syncDirOut = (& $mcas sync-dir --from claude --to agy --cwd $wsB --json) -join "`n"
    $syncDirObj = $syncDirOut | ConvertFrom-Json
    $wsBAgyCount = @(((& $mcas list --cwd $wsB --source agy --json) -join "`n") | ConvertFrom-Json).Count
    Check 'cli: sync-dir copies wsB session to agy' ($wsBAgyCount -ge 1 -and @($syncDirObj.results).Count -ge 1)

    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $badOut = & $mcas nope 2>&1
    $badExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    Check 'cli: unknown command exits 1' ($badExit -eq 1 -and (Has $badOut 'Unknown command'))

    $ErrorActionPreference = 'Continue'
    $unsupOut = & $mcas copy $agySeedId --to opencode 2>&1
    $unsupExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    Check 'cli: unsupported future provider fails cleanly' ($unsupExit -ne 0 -and (Has $unsupOut 'opencode|coming soon|supported'))

    # Autostart CLI
    $startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\mcas.lnk'
    Check 'cli: autostart on' ((Has (& $mcas autostart on) 'start in the tray') -and (Test-Path -LiteralPath $startup))
    Check 'cli: autostart off' ((Has (& $mcas autostart off) 'not start') -and -not (Test-Path -LiteralPath $startup))

    # Autosync CLI
    $asStatus1 = ((& $mcas autosync status --json) -join "`n") | ConvertFrom-Json
    Check 'cli: autosync initially off' (-not $asStatus1.autosync.enabled)
    $null = & $mcas autosync on --direction both --scope all --interval 15
    $asStatus2 = ((& $mcas autosync status --json) -join "`n") | ConvertFrom-Json
    Check 'cli: autosync on sets interval' ($asStatus2.autosync.enabled -and [int]$asStatus2.autosync.intervalSec -eq 15)
    $null = & $mcas autosync off

    # Web UI + API tests
    $uiStartOut = & $mcas ui --no-browser
    if ($LASTEXITCODE -ne 0) { throw "mcas ui --no-browser failed: $uiStartOut" }
    $u = "http://127.0.0.1:$env:MCAS_PORT"
    $health = Invoke-RestMethod "$u/health" -UseBasicParsing
    Check 'ui: /health ok' ($health.app -eq 'mcas')

    $page = (Invoke-WebRequest "$u/" -UseBasicParsing).Content
    $tok = [regex]::Match($page, "const token = '([0-9a-f]{32})'").Groups[1].Value
    Check 'ui: token extracted from /' ($tok.Length -eq 32)
    $h = @{ 'X-Mcas-Token' = $tok }

    $code = try { Invoke-WebRequest "$u/api/status" -UseBasicParsing | Out-Null; 200 } catch { [int]$_.Exception.Response.StatusCode }
    Check 'ui: no token -> 403' ($code -eq 403)

    $st = Invoke-RestMethod "$u/api/status" -Headers $h -UseBasicParsing
    Check 'ui: /api/status providers & workspaces' (@($st.providers | Where-Object { $_.id -eq 'opencode' -and $_.comingSoon }).Count -eq 1 -and @($st.workspaces).Count -ge 2)

    $sessResp = Invoke-RestMethod "$u/api/sessions?source=all&limit=50" -Headers $h -UseBasicParsing
    Check 'ui: /api/sessions returns list' ($sessResp.total -ge 3 -and @($sessResp.sessions).Count -ge 3)

    $oneSess = Invoke-RestMethod "$u/api/session?source=agy&id=$agySeedId" -Headers $h -UseBasicParsing
    Check 'ui: /api/session returns turns' ($oneSess.id -eq $agySeedId -and @($oneSess.turns).Count -ge 3)

    $apiCopy = Invoke-McasJsonPost "$u/api/copy" $h @{
        items = @(@{ source = 'agy'; id = $agySeedId })
        toProvider = 'claude'
        newId = $true
        overwrite = $false
        includeThinking = $false
    }
    Check 'ui: /api/copy succeeds' ($apiCopy.results[0].ok -and $apiCopy.results[0].resumeCmd -match 'claude --resume')
    $copiedTargetId = $apiCopy.results[0].targetId

    $apiSyncDir = Invoke-McasJsonPost "$u/api/sync-dir" $h @{
        fromProvider = 'claude'
        toProvider = 'agy'
        cwd = $wsA
        overwrite = $false
        includeThinking = $false
    }
    Check 'ui: /api/sync-dir succeeds' (@($apiSyncDir.results).Count -ge 1)

    # Loop-free Auto-Sync test in isolated watched folder $wsC
    $seedAutoPy = @'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent))
import seed
seed.make_agy_db(
    sys.argv[2],
    sys.argv[3],
    "AutoSync Loop Test",
    "AutoSync user message 1",
    "",
    "AutoSync reply 1",
    1763112000,
)
'@
    $seedAutoPath = Join-Path $sb 'seed_auto.py'
    [IO.File]::WriteAllText($seedAutoPath, $seedAutoPy, [Text.Encoding]::UTF8)
    & $pyExe @($pyPre + @('-B', $seedAutoPath, $seedPath, $agyAutoId, $wsC))
    if ($LASTEXITCODE -ne 0) { throw 'Failed to seed wsC session.' }

    $asRun1 = Invoke-McasJsonPost "$u/api/autosync" $h @{
        enabled = $false
        direction = 'both'
        scope = 'watched'
        watchedFolders = @($wsC)
        includeThinking = $false
        runNow = $true
    }
    $wsCSlug = Get-ClaudeSlug $wsC
    $wsCClaudeDir = Join-Path $env:MCAS_CLAUDE_DIR ("projects\" + $wsCSlug)
    $wsCFiles1 = @(Get-ChildItem -LiteralPath $wsCClaudeDir -Filter '*.jsonl' -ErrorAction SilentlyContinue)
    Check 'ui: autosync runNow synced wsC session once' ($wsCFiles1.Count -eq 1)
    $targetFile = $wsCFiles1[0]
    $ticks1 = $targetFile.LastWriteTimeUtc.Ticks
    $len1 = $targetFile.Length
    $hist1 = @($asRun1.autosync.history).Count

    # Second immediate runNow MUST be a complete no-op (no echo loop, no rewrite)
    Start-Sleep -Milliseconds 150
    $asRun2 = Invoke-McasJsonPost "$u/api/autosync" $h @{ runNow = $true }
    $wsCFiles2 = @(Get-ChildItem -LiteralPath $wsCClaudeDir -Filter '*.jsonl' -ErrorAction SilentlyContinue)
    $targetFile2 = Get-Item -LiteralPath $targetFile.FullName
    Check 'ui: autosync 2nd runNow is loop-free idempotent' (
        $wsCFiles2.Count -eq 1 -and
        $targetFile2.LastWriteTimeUtc.Ticks -eq $ticks1 -and
        $targetFile2.Length -eq $len1 -and
        [int]$asRun2.runResult.synced -eq 0 -and
        @($asRun2.autosync.history).Count -eq $hist1
    )

    # Append a new turn to the synced Claude JSONL and verify autosync updates agy in-place, then stays quiet
    $extraTurns = @(
        (ConvertTo-Json -Compress @{
            type = 'user'; uuid = 'u-auto-2'; parentUuid = $null; sessionId = $targetFile.BaseName
            isSidechain = $false; timestamp = '2026-11-14T09:50:00.000Z'; cwd = $wsC
            message = @{ role = 'user'; content = 'Follow-up turn from Claude side' }
        }),
        (ConvertTo-Json -Compress @{
            type = 'assistant'; uuid = 'a-auto-2'; parentUuid = 'u-auto-2'; sessionId = $targetFile.BaseName
            isSidechain = $false; timestamp = '2026-11-14T09:50:05.000Z'; cwd = $wsC
            message = @{ id = 'msg_auto_2'; role = 'assistant'; content = @(@{ type = 'text'; text = 'Reply echoed cleanly back to AGY' }) }
        })
    ) -join "`n"
    [IO.File]::AppendAllText($targetFile.FullName, $extraTurns + "`n", [Text.Encoding]::UTF8)

    $asRun3 = Invoke-McasJsonPost "$u/api/autosync" $h @{ runNow = $true }
    $agyUpdated = Invoke-RestMethod "$u/api/session?source=agy&id=$agyAutoId" -Headers $h -UseBasicParsing
    Check 'ui: autosync propagates new Claude turn back to paired agy DB' (
        @($agyUpdated.turns | Where-Object { $_.text -match 'Follow-up turn from Claude side' }).Count -eq 1
    )
    $hist3 = @($asRun3.autosync.history).Count
    $asRun4 = Invoke-McasJsonPost "$u/api/autosync" $h @{ runNow = $true }
    Check 'ui: autosync 4th runNow after reverse update is loop-free' (
        [int]$asRun4.runResult.synced -eq 0 -and
        @($asRun4.autosync.history).Count -eq $hist3
    )

    # Autostart via API
    $asOn = Invoke-McasJsonPost "$u/api/autostart" $h @{ on = $true }
    Check 'ui: /api/autostart on' ($asOn.autostart -and (Test-Path -LiteralPath $startup))
    $asOff = Invoke-McasJsonPost "$u/api/autostart" $h @{ on = $false }
    Check 'ui: /api/autostart off' (-not $asOff.autostart -and -not (Test-Path -LiteralPath $startup))

    # Enriched syncedTo + status.storage checks
    $st2 = Invoke-RestMethod "$u/api/status" -Headers $h -UseBasicParsing
    Check 'ui: /api/status includes storage summary' ($null -ne $st2.storage -and [int64]$st2.storage.totalBytes -gt 0 -and $st2.storage.totalHuman)
    $wsCSess = Invoke-RestMethod "$u/api/sessions?source=agy&cwd=$([uri]::EscapeDataString($wsC))&limit=50" -Headers $h -UseBasicParsing
    $autoEntry = @($wsCSess.sessions | Where-Object { $_.id -eq $agyAutoId })[0]
    Check 'ui: syncedTo includes enriched sizeBytes & title' ($null -ne $autoEntry -and @($autoEntry.syncedTo).Count -ge 1 -and [int64]$autoEntry.syncedTo[0].sizeBytes -gt 0 -and $autoEntry.syncedTo[0].title)

    # Size sorting, trim/lighten, clean, and batch synced peer deletion tests
    $heavyClaudeId = '66666666-6666-4666-8666-666666666666'
    $emptyClaudeId = '77777777-7777-4777-8777-777777777777'
    $orphanBrainDir = Join-Path $env:MCAS_AGY_DIR 'brain\orphan-brain-123'
    $orphanFhDir = Join-Path $env:MCAS_CLAUDE_DIR 'file-history\orphan-fh-123'
    $seedCleanPy = @'
import json, sys
from pathlib import Path

ws_a = sys.argv[1]
slug_a = sys.argv[2]
claude_dir = Path(sys.argv[3])
agy_dir = Path(sys.argv[4])
heavy_id = sys.argv[5]
empty_id = sys.argv[6]
agy_seed_id = sys.argv[7]

proj_a = claude_dir / "projects" / slug_a
proj_a.mkdir(parents=True, exist_ok=True)

# Heavy Claude session with 4 turns, thinking, >4KB tool output, file-history, and subagent sidecar
heavy_rows = [
    {
        "type": "user", "uuid": "h-u1", "parentUuid": None, "sessionId": heavy_id,
        "isSidechain": False, "timestamp": "2026-11-14T08:00:00.000Z", "cwd": ws_a,
        "message": {"role": "user", "content": "Heavy question 1"}
    },
    {
        "type": "assistant", "uuid": "h-a1", "parentUuid": "h-u1", "sessionId": heavy_id,
        "isSidechain": False, "timestamp": "2026-11-14T08:00:05.000Z", "cwd": ws_a,
        "message": {
            "id": "msg_h1", "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "Long internal thinking " * 20},
                {"type": "text", "text": "Running heavy tool"},
                {"type": "tool_use", "id": "tu_h1", "name": "read_file", "input": {"path": "big.txt"}}
            ]
        }
    },
    {
        "type": "user", "uuid": "h-tr1", "parentUuid": "h-a1", "sessionId": heavy_id,
        "isSidechain": False, "timestamp": "2026-11-14T08:00:08.000Z", "cwd": ws_a,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu_h1", "content": "X" * 4500}]
        }
    },
    {
        "type": "user", "uuid": "h-u2", "parentUuid": "h-tr1", "sessionId": heavy_id,
        "isSidechain": False, "timestamp": "2026-11-14T08:01:00.000Z", "cwd": ws_a,
        "message": {"role": "user", "content": "Followup question 2"}
    },
    {
        "type": "assistant", "uuid": "h-a2", "parentUuid": "h-u2", "sessionId": heavy_id,
        "isSidechain": False, "timestamp": "2026-11-14T08:01:05.000Z", "cwd": ws_a,
        "message": {
            "id": "msg_h2", "role": "assistant",
            "content": [{"type": "text", "text": "Newest final answer"}]
        }
    }
]
(proj_a / f"{heavy_id}.jsonl").write_text(
    "\n".join(json.dumps(r) for r in heavy_rows) + "\n", encoding="utf-8"
)
sidecar = proj_a / heavy_id
(sidecar / "subagents").mkdir(parents=True, exist_ok=True)
(sidecar / "subagents" / "agent-heavy.jsonl").write_bytes(b"S" * 40000)
(sidecar / "custom-title.json").write_text('{"customTitle": "Heavy Test Chat"}', encoding="utf-8")
fh_heavy = claude_dir / "file-history" / heavy_id
fh_heavy.mkdir(parents=True, exist_ok=True)
(fh_heavy / "snap.bin").write_bytes(b"F" * 60000)

# Empty 0-turn Claude session
empty_row = {
    "type": "file-history-snapshot", "messageId": "m-empty-1",
    "snapshot": {"messageId": "m-empty-1", "trackedFileBackups": {}, "timestamp": "2026-11-14T07:00:00.000Z"}
}
(proj_a / f"{empty_id}.jsonl").write_text(json.dumps(empty_row) + "\n", encoding="utf-8")

# Sidecar for agySeedId + orphans
br_seed = agy_dir / "brain" / agy_seed_id
br_seed.mkdir(parents=True, exist_ok=True)
(br_seed / "artifact.bin").write_bytes(b"A" * 50000)
br_orphan = agy_dir / "brain" / "orphan-brain-123"
br_orphan.mkdir(parents=True, exist_ok=True)
(br_orphan / "junk.bin").write_bytes(b"O" * 16384)
fh_orphan = claude_dir / "file-history" / "orphan-fh-123"
fh_orphan.mkdir(parents=True, exist_ok=True)
(fh_orphan / "old.bak").write_bytes(b"O" * 16384)
'@
    $seedCleanPath = Join-Path $sb 'seed_clean.py'
    [IO.File]::WriteAllText($seedCleanPath, $seedCleanPy, [Text.Encoding]::UTF8)
    $wsASlug = Get-ClaudeSlug $wsA
    & $pyExe @($pyPre + @('-B', $seedCleanPath, $wsA, $wsASlug, $env:MCAS_CLAUDE_DIR, $env:MCAS_AGY_DIR, $heavyClaudeId, $emptyClaudeId, $agySeedId))
    if ($LASTEXITCODE -ne 0) { throw 'Failed to seed cleanup/trim test data.' }

    # CLI & API size sorting
    $listBySize = ((& $mcas list --sort size --json) -join "`n") | ConvertFrom-Json
    Check 'cli: list --sort size descending with size_bytes' (
        @($listBySize).Count -ge 3 -and
        [int64]$listBySize[0].size_bytes -ge [int64]$listBySize[1].size_bytes -and
        [string]$listBySize[0].size_human -match 'B$'
    )
    $apiBySize = Invoke-RestMethod "$u/api/sessions?source=all&sort=size_desc&limit=50" -Headers $h -UseBasicParsing
    Check 'ui: /api/sessions?sort=size_desc sorts largest first' (
        @($apiBySize.sessions).Count -ge 3 -and
        [int64]$apiBySize.sessions[0].sizeBytes -ge [int64]$apiBySize.sessions[1].sizeBytes
    )

    # CLI trim
    $cliTrim = ((& $mcas trim $heavyClaudeId --source claude --keep-ratio 0.5 --yes --json) -join "`n") | ConvertFrom-Json
    $heavyCustomTitle = Join-Path $env:MCAS_CLAUDE_DIR ("projects\" + $wsASlug + "\" + $heavyClaudeId + "\custom-title.json")
    $heavySubDir = Join-Path $env:MCAS_CLAUDE_DIR ("projects\" + $wsASlug + "\" + $heavyClaudeId + "\subagents")
    Check 'cli: trim shrinks session, purges sidecars, preserves custom-title' (
        $cliTrim.ok -and
        [int]$cliTrim.turnsAfter -lt [int]$cliTrim.turnsBefore -and
        [int64]$cliTrim.afterBytes -lt [int64]$cliTrim.beforeBytes -and
        [int64]$cliTrim.freedBytes -gt 50000 -and
        -not (Test-Path -LiteralPath $heavySubDir) -and
        (Test-Path -LiteralPath $heavyCustomTitle)
    )
    $heavyInspect = ((& $mcas inspect $heavyClaudeId --source claude --json) -join "`n") | ConvertFrom-Json
    Check 'cli: trimmed claude session still inspects cleanly' ($heavyInspect.id -eq $heavyClaudeId -and @($heavyInspect.turns).Count -ge 1)

    # API trim on agy
    $apiTrim = Invoke-McasJsonPost "$u/api/trim" $h @{
        source = 'agy'
        id = $agySeedId
        keepLastTurns = 2
        purgeSidecars = $true
        stripThinking = $true
    }
    Check 'ui: /api/trim lightens agy session and removes brain dir' (
        $apiTrim.ok -and
        [int64]$apiTrim.freedBytes -gt 20000 -and
        -not (Test-Path -LiteralPath (Join-Path $env:MCAS_AGY_DIR ("brain\" + $agySeedId)))
    )

    # API dry-run clean + CLI clean
    $cleanDry = Invoke-McasJsonPost "$u/api/clean" $h @{
        source = 'all'
        cleanOrphans = $true
        deleteEmpty = $true
        dryRun = $true
    }
    Check 'ui: /api/clean dryRun reports targets without deleting' (
        $cleanDry.dryRun -and
        [int]$cleanDry.orphansCleaned -ge 2 -and
        [int]$cleanDry.sessionsDeleted -ge 1 -and
        (Test-Path -LiteralPath $orphanBrainDir) -and
        (Test-Path -LiteralPath $orphanFhDir)
    )
    $cliClean = ((& $mcas clean --empty --yes --json) -join "`n") | ConvertFrom-Json
    $emptyJsonl = Join-Path $env:MCAS_CLAUDE_DIR ("projects\" + $wsASlug + "\" + $emptyClaudeId + ".jsonl")
    Check 'cli: clean removes orphans and empty sessions' (
        $cliClean.ok -and -not $cliClean.dryRun -and
        [int]$cliClean.orphansCleaned -ge 2 -and
        [int]$cliClean.sessionsDeleted -ge 1 -and
        -not (Test-Path -LiteralPath $orphanBrainDir) -and
        -not (Test-Path -LiteralPath $orphanFhDir) -and
        -not (Test-Path -LiteralPath $emptyJsonl)
    )

    # Delete via API (single + synced peers batch)
    $null = Invoke-McasJsonPost "$u/api/delete" $h @{ source = 'claude'; id = $copiedTargetId }
    $afterDel = Invoke-RestMethod "$u/api/sessions?source=claude&limit=200" -Headers $h -UseBasicParsing
    Check 'ui: /api/delete removes session' (@($afterDel.sessions | Where-Object { $_.id -eq $copiedTargetId }).Count -eq 0)

    $delSynced = Invoke-McasJsonPost "$u/api/delete" $h @{
        items = @(@{ source = 'agy'; id = $agyAutoId })
        deleteSyncedPeers = $true
    }
    Check 'ui: /api/delete with deleteSyncedPeers removes both paired copies' (
        $delSynced.ok -and
        [int]$delSynced.deletedCount -ge 2 -and
        -not (Test-Path -LiteralPath $targetFile.FullName)
    )

    $uiPid = [int](Get-Content -LiteralPath (Join-Path $root 'ui.json') -Raw | ConvertFrom-Json).pid
    Check 'ui: server stays up' ((Get-Process -Id $uiPid -ErrorAction SilentlyContinue) -ne $null)

    # Update while UI server is running (stops server, replaces files, restarts server)
    $updOut = & $mcas update
    Check 'update: installs and keeps sync_state' ((Has $updOut 'installed') -and (Test-Path -LiteralPath (Join-Path $root 'sync_state.json')))
    $healthAfterUpd = Invoke-RestMethod "$u/health" -UseBasicParsing
    Check 'update: restarts running UI server' ($healthAfterUpd.app -eq 'mcas')

    & $mcas ui stop | Out-Null
    Check 'ui stop: server gone' ((Get-PortState $env:MCAS_PORT) -eq 'free')

    # Uninstall with --purge -y
    & $mcas uninstall --purge -y | Out-Null
    $uninstDeadline = (Get-Date).AddSeconds(8)
    while ((Test-Path -LiteralPath $root) -and (Get-Date) -lt $uninstDeadline) { Start-Sleep -Milliseconds 250 }
    Check 'uninstall: mcas folder removed' (-not (Test-Path -LiteralPath $root))
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
    $clean = (@($p -split ';' | Where-Object { $_ -and $_ -notlike "$sb*" })) -join ';'
    $realNorm = (@($realPath -split ';' | Where-Object { $_ })) -join ';'
    if ($clean -ne $p) { [Environment]::SetEnvironmentVariable('Path', $clean, 'User') }
    if ($clean -eq $realNorm -and $realNorm -ne $realPath) { [Environment]::SetEnvironmentVariable('Path', $realPath, 'User') }
    $finalPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    Check 'user PATH unchanged' (($finalPath -eq $realPath) -or ($finalPath -eq $realNorm))
    cmd /c rmdir /s /q "`"$sb`"" 2>$null
}

if ($fail) { Write-Host "$fail check(s) failed" -ForegroundColor Red; exit 1 }
Write-Host 'all checks passed' -ForegroundColor Green
