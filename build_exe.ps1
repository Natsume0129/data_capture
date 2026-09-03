param(
    [string]$FfmpegPath = "C:\ffmpeg\bin\ffmpeg.exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv313\Scripts\python.exe"
$questionSet = Join-Path $projectRoot "question_set"
$distRoot = Join-Path $projectRoot "dist\ScenarioCapture"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found: $python"
}
if (-not (Test-Path -LiteralPath $FfmpegPath -PathType Leaf)) {
    throw "FFmpeg not found: $FfmpegPath"
}
if (-not (Test-Path -LiteralPath $questionSet -PathType Container)) {
    throw "Question-set directory not found: $questionSet"
}

$pyinstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--windowed",
    "--onedir",
    "--name", "ScenarioCapture",
    "--collect-submodules", "google.cloud.texttospeech",
    "app.py"
)

Push-Location $projectRoot
try {
    & $python -m PyInstaller @pyinstallerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    Copy-Item -LiteralPath $FfmpegPath -Destination (Join-Path $distRoot "ffmpeg.exe")
    Copy-Item -LiteralPath $questionSet -Destination $distRoot -Recurse
    Write-Output "Built application: $(Join-Path $distRoot 'ScenarioCapture.exe')"
}
finally {
    Pop-Location
}
