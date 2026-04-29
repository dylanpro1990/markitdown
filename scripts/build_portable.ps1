param(
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Virtual environment Python was not found: $venvPython"
}

$modeArgs = @("--noconfirm", "--windowed", "--name", "MarkItDownPortable")
if ($OneFile) {
    $modeArgs += "--onefile"
}

& $venvPython -m PyInstaller `
    @modeArgs `
    --paths "packages/markitdown/src" `
    --collect-all markitdown `
    --collect-all magika `
    --collect-all onnxruntime `
    --collect-all yt_dlp `
    --hidden-import tkinter `
    --hidden-import tkinter.ttk `
    --hidden-import tkinter.filedialog `
    --hidden-import tkinter.messagebox `
    --hidden-import tkinterdnd2 `
    "packages/markitdown/src/markitdown/desktop_app.py"

Write-Host ""
Write-Host "Build completed." -ForegroundColor Green
if ($OneFile) {
    Write-Host "Portable executable: dist\MarkItDownPortable.exe"
} else {
    Write-Host "Portable folder: dist\MarkItDownPortable\"
    Write-Host "Launch file: dist\MarkItDownPortable\MarkItDownPortable.exe"
}
