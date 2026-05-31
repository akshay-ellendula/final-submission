$ErrorActionPreference = "Stop"

$ROOT = (Get-Item $MyInvocation.MyCommand.Path).Directory.Parent.FullName
Set-Location $ROOT

$API_URL = if ($env:API_URL) { $env:API_URL } else { "http://localhost:8000" }
$CCTV_DIR = if ($env:CCTV_DIR) { $env:CCTV_DIR } else { "data-provided\CCTV Footage" }
$LAYOUT = if ($env:LAYOUT) { $env:LAYOUT } else { "config\store_layout.json" }
$FPS = if ($env:FPS) { $env:FPS } else { "5" }
$STORE = if ($env:STORE_ID) { $env:STORE_ID } else { "ST1008" }
$PYTHON = ".\.venv\Scripts\python.exe"

New-Item -ItemType Directory -Force -Path data | Out-Null
Set-Content -Path data\events.jsonl -Value $null

$MAPPING = @(
    "CAM_ENTRY_01|CAM 3.mp4",
    "CAM_FLOOR_SKIN|CAM 1.mp4",
    "CAM_FLOOR_MAKEUP|CAM 2.mp4",
    "CAM_CASH_COUNTER|CAM 5.mp4",
    "CAM_STOCKROOM|CAM 4.mp4"
)

$START_TS = if ($env:START_TS) { $env:START_TS } else { (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") }
Write-Host "[run_windows.ps1] start_ts=$START_TS  api=$API_URL  fps=$FPS  store=$STORE"

foreach ($entry in $MAPPING) {
    $parts = $entry.Split('|')
    $cam = $parts[0]
    $clip = $parts[1]
    $clip_path = Join-Path $CCTV_DIR $clip
    
    if (-not (Test-Path $clip_path)) {
        Write-Host "[run_windows.ps1] SKIP $cam - missing $clip_path"
        continue
    }
    Write-Host "[run_windows.ps1] processing $cam from $clip"
    & $PYTHON -m pipeline.detect `
        --clip "$clip_path" `
        --camera-id "$cam" `
        --store-id "$STORE" `
        --layout "$LAYOUT" `
        --api-url "$API_URL" `
        --jsonl "data\events.jsonl" `
        --start-ts "$START_TS" `
        --fps "$FPS"
}

Write-Host "[run_windows.ps1] posting real POS transactions"
& $PYTHON -m pipeline.post_pos `
    --csv "data-provided\Brigade_Bangalore_10_April_26 (1)bc6219c.csv" `
    --api-url "$API_URL"

Write-Host "[run_windows.ps1] done. Events in data\events.jsonl, POS ingested from real data."
