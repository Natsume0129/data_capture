param([string]$FFmpeg = "")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv313\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Python environment missing: $python" }
if ($FFmpeg) {
    & $python (Join-Path $projectRoot "annotation_app.py") --ffmpeg $FFmpeg
} else {
    & $python (Join-Path $projectRoot "annotation_app.py")
}
