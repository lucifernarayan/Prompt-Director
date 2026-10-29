# scripts/chaos.ps1 - Operator Chaos Harness
param (
    [Parameter(Position=0)]
    [ValidateSet("t7", "t8", "t11", "stop-redis", "start-redis", "stop-worker", "start-worker", "stop-postgres", "start-postgres", "restore", "help")]
    [string]$Action = "help"
)

$ErrorActionPreference = "Continue"

function Run-T7 {
    Write-Host "=== Running T7 Killed Worker Fault Tolerance Test ==="
    & "$PSScriptRoot\t7.ps1"
}

function Run-T8 {
    Write-Host "=== Running T8 20-Request Single-Flight Coalescing Test ==="
    docker compose run --rm api python -m pytest -q tests/test_cache.py -k test_t8_twenty_identical_concurrent_requests_single_flight
}

function Run-T11 {
    Write-Host "=== Running T11 SSE Reconnect Replay Guarantee Test ==="
    docker compose run --rm api python -m pytest -q tests/test_sse.py -k test_t11_sse_reconnect_with_last_event_id_no_missing_no_duplicates
}

function Stop-Redis {
    Write-Host "Stopping Redis container..."
    docker compose stop redis
}

function Start-Redis {
    Write-Host "Starting Redis container..."
    docker compose start redis
}

function Stop-Worker {
    Write-Host "Stopping one worker container..."
    $cid = docker compose ps -q worker | Select-Object -First 1
    if ($cid) {
        docker stop $cid
    } else {
        docker compose stop worker
    }
}

function Start-Worker {
    Write-Host "Starting worker containers..."
    docker compose up -d --scale worker=2
}

function Stop-Postgres {
    Write-Host "Stopping Postgres container..."
    docker compose stop postgres
}

function Start-Postgres {
    Write-Host "Starting Postgres container..."
    docker compose start postgres
}

function Restore-All {
    Write-Host "Restoring full Docker Compose stack with 2 workers..."
    docker compose start redis
    docker compose start postgres
    docker compose up -d --scale worker=2 api
    Write-Host "Stack restored."
}

switch ($Action.ToLower()) {
    "t7"             { Run-T7 }
    "t8"             { Run-T8 }
    "t11"            { Run-T11 }
    "stop-redis"     { Stop-Redis }
    "start-redis"    { Start-Redis }
    "stop-worker"    { Stop-Worker }
    "start-worker"   { Start-Worker }
    "stop-postgres"  { Stop-Postgres }
    "start-postgres" { Start-Postgres }
    "restore"        { Restore-All }
    default {
        Write-Host "Usage: .\scripts\chaos.ps1 [t7 | t8 | t11 | stop-redis | start-redis | stop-worker | start-worker | stop-postgres | start-postgres | restore]"
    }
}
