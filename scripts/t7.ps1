# scripts/t7.ps1 - T7 Killed Worker Fault Tolerance Test
$ErrorActionPreference = "Continue"

Write-Host "=== Starting T7 Killed Worker Test ==="

# 1. Ensure stack is up with 2 workers
Write-Host "Ensuring Docker Compose stack is running with 2 workers..."
docker compose up -d --scale worker=2

# Wait for /healthz to return 200 and for the seed to exist before submitting (poll up to 60 s)
Write-Host "Waiting for /healthz to return 200 and demo seed to exist (poll up to 60 s)..."
$health_deadline = (Get-Date).AddSeconds(60)
$stack_ready = $false

while ((Get-Date) -lt $health_deadline) {
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:8000/healthz" -Method Get -TimeoutSec 2 -ErrorAction Stop
        if ($resp.db -eq "ok" -and $resp.redis -eq "ok") {
            # Verify seed exists (user_a is member of proj_a)
            $seed_check = docker compose run --rm api python -c "import psycopg, sys; from app.config import settings; conn = psycopg.connect(settings.DATABASE_URL); cur = conn.cursor(); cur.execute('''SELECT 1 FROM project_members WHERE user_id = %s''', ('user_a',)); sys.exit(0 if cur.fetchone() else 1)"
            if ($LASTEXITCODE -eq 0) {
                $stack_ready = $true
                Write-Host "API /healthz returned 200 and fixtures seed verified!"
                break
            }
        }
    } catch {
        # Retry until deadline
    }
    Start-Sleep -Seconds 2
}

if (-not $stack_ready) {
    Write-Host "FAIL: API /healthz did not return 200 or fixtures seed was not found within 60s."
    exit 1
}

# 2. Set chaos switch pause_after:draft=30
Write-Host "Setting chaos switch pause_after:draft=30..."
docker compose run --rm api python -m app.llm.chaos_cli set pause_after:draft 30

# 3. Submit a job with the submit tool
Write-Host "Submitting test job..."
$submit_output = docker compose run --rm api python -m app.tools.submit user_a proj_a "A detective investigating in neon rain"
$job_id = ($submit_output | Where-Object { $_ -match '^[0-9a-f\-]{36}$' } | Select-Object -Last 1).Trim()

if (-not $job_id) {
    # Fallback to last line
    $job_id = ($submit_output[-1]).Trim()
}

Write-Host "Submitted job ID: $job_id"
if (-not $job_id) {
    Write-Host "FAIL: Failed to get job_id from submit tool"
    Write-Host "Submit output: $submit_output"
    exit 1
}

# 4. Poll inspect until checkpoints show node_done='draft'
Write-Host "Polling inspect until checkpoint draft is reached..."
$killed_container = $null
$killed_hostname = $null
$deadline = (Get-Date).AddSeconds(45)
$draft_reached = $false

while ((Get-Date) -lt $deadline) {
    $inspect_raw = docker compose run --rm api python -m app.tools.inspect $job_id
    # Extract JSON from inspect output (in case Docker prefixes container lines)
    $json_str = $inspect_raw -join "`n"
    $start_idx = $json_str.IndexOf('{')
    if ($start_idx -ge 0) {
        $json_str = $json_str.Substring($start_idx)
        try {
            $data = $json_str | ConvertFrom-Json
            $ckpts = $data.checkpoints | Where-Object { $_.node_done -eq "draft" }
            if ($ckpts) {
                $killed_hostname = $data.lease_owner
                $draft_reached = $true
                Write-Host "Checkpoint 'draft' reached! Lease owner: $killed_hostname"
                break
            }
        } catch {
            # continue polling
        }
    }
    Start-Sleep -Seconds 1
}

if (-not $draft_reached) {
    Write-Host "FAIL: Did not reach checkpoint 'draft' within deadline"
    docker compose run --rm api python -m app.llm.chaos_cli clear pause_after:draft
    docker compose up -d
    Write-Host "T7 FAIL"
    exit 1
}

# 5. Find the matching worker container for lease_owner
Write-Host "Finding container for worker hostname: $killed_hostname..."
$worker_containers = @(docker compose ps -q worker)
$all_worker_ids = @($worker_containers)
$matched_cid = $null

foreach ($cid in $worker_containers) {
    $short_id = $cid.Substring(0, [Math]::Min(12, $cid.Length))
    $hostname = (docker inspect --format '{{.Config.Hostname}}' $cid).Trim()
    if ($short_id -eq $killed_hostname -or $hostname -eq $killed_hostname) {
        $matched_cid = $cid
        break
    }
}

if (-not $matched_cid) {
    Write-Host "Warning: Exact container match not found by hostname; picking first active worker container"
    $matched_cid = $worker_containers[0]
}

Write-Host "Killing worker container: $matched_cid (hostname: $killed_hostname)..."
docker kill $matched_cid

# Record all worker container IDs (including the killed one) for log verification
$all_worker_ids = $worker_containers

# 6. Wait up to 60s for status 'done' (or 'delivered')
Write-Host "Waiting up to 60s for job to finish..."
$done_deadline = (Get-Date).AddSeconds(60)
$is_done = $false
$final_data = $null

while ((Get-Date) -lt $done_deadline) {
    $inspect_raw = docker compose run --rm api python -m app.tools.inspect $job_id
    $json_str = $inspect_raw -join "`n"
    $start_idx = $json_str.IndexOf('{')
    if ($start_idx -ge 0) {
        $json_str = $json_str.Substring($start_idx)
        try {
            $data = $json_str | ConvertFrom-Json
            if ($data.status -eq "done" -or $data.raw_status -eq "delivered") {
                $is_done = $true
                $final_data = $data
                Write-Host "Job finished with status: $($data.status)!"
                break
            }
        } catch {}
    }
    Start-Sleep -Seconds 2
}

if (-not $is_done) {
    Write-Host "FAIL: Job did not reach status 'done' within 60s"
    docker compose run --rm api python -m app.llm.chaos_cli clear pause_after:draft
    docker compose up -d
    Write-Host "T7 FAIL"
    exit 1
}

Write-Host "=== Verifying T7 Assertions ==="
$all_passed = $true

# Assertion 1: status done
if ($final_data.status -eq "done" -or $final_data.raw_status -eq "delivered") {
    Write-Host "ASSERT status done: PASS"
} else {
    Write-Host "ASSERT status done: FAIL ($($final_data.status))"
    $all_passed = $false
}

# Assertion 2: every call_key appears exactly once in llm_calls
$call_keys = @($final_data.llm_calls | ForEach-Object { $_.call_key })
$unique_keys = @($call_keys | Select-Object -Unique)
if ($call_keys.Count -gt 0 -and $call_keys.Count -eq $unique_keys.Count) {
    Write-Host "ASSERT every call_key appears exactly once in llm_calls: PASS (count=$($call_keys.Count))"
} else {
    Write-Host "ASSERT every call_key appears exactly once in llm_calls: FAIL (total=$($call_keys.Count), unique=$($unique_keys.Count))"
    $all_passed = $false
}

# Assertion 3: draft call_key appears exactly once in provider_call log lines across ALL worker containers
$draft_provider_calls = 0
foreach ($cid in $all_worker_ids) {
    $logs = docker logs $cid 2>&1
    foreach ($line in $logs) {
        if ($line -match '"event":\s*"provider_call"' -and $line -match $job_id -and $line -match '"call_key":\s*"draft:') {
            $draft_provider_calls++
        }
    }
}

if ($draft_provider_calls -eq 1) {
    Write-Host "ASSERT draft call_key appears exactly once in provider_call logs: PASS (count=$draft_provider_calls)"
} else {
    Write-Host "ASSERT draft call_key appears exactly once in provider_call logs: FAIL (count=$draft_provider_calls, expected 1)"
    $all_passed = $false
}

# Assertion 4: exactly one 'settle' ledger row
$settle_rows = @($final_data.ledger | Where-Object { $_.phase -eq "settle" })
if ($settle_rows.Count -eq 1) {
    Write-Host "ASSERT exactly one 'settle' ledger row: PASS"
} else {
    Write-Host "ASSERT exactly one 'settle' ledger row: FAIL (count=$($settle_rows.Count))"
    $all_passed = $false
}

# Assertion 5: charged_micro equals quote_micro
if ($final_data.charged_micro -gt 0 -and $final_data.charged_micro -eq $final_data.quote_micro) {
    Write-Host "ASSERT charged_micro equals quote_micro: PASS ($($final_data.charged_micro) == $($final_data.quote_micro))"
} else {
    Write-Host "ASSERT charged_micro equals quote_micro: FAIL ($($final_data.charged_micro) != $($final_data.quote_micro))"
    $all_passed = $false
}

# Assertion 6: the job's final lease_owner differs from the killed worker
if ($final_data.lease_owner -ne $killed_hostname) {
    Write-Host "ASSERT final lease_owner differs from killed worker: PASS ($($final_data.lease_owner) != $killed_hostname)"
} else {
    Write-Host "ASSERT final lease_owner differs from killed worker: FAIL (final=$($final_data.lease_owner), killed=$killed_hostname)"
    $all_passed = $false
}

# 7. Cleanup & restore stack
Write-Host "Cleaning up chaos switch and restoring worker containers..."
docker compose run --rm api python -m app.llm.chaos_cli clear pause_after:draft
docker compose up -d --scale worker=2

if ($all_passed) {
    Write-Host "T7 PASS"
    exit 0
} else {
    Write-Host "T7 FAIL"
    exit 1
}
