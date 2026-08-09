# Packages the addon into a zip Blender's Extensions system can install.
$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$distDir = Join-Path $root "dist"
$zipPath = Join-Path $distDir "techart_tools.zip"

New-Item -ItemType Directory -Force -Path $distDir | Out-Null
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

$files = @("blender_manifest.toml", "__init__.py", "operators.py", "checker.py", "ui.py")
$tempDir = Join-Path $distDir "techart_tools"
if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

foreach ($f in $files) {
    Copy-Item (Join-Path $root $f) (Join-Path $tempDir $f)
}

Copy-Item (Join-Path $root "checkers") (Join-Path $tempDir "checkers") -Recurse
Copy-Item (Join-Path $root "textures") (Join-Path $tempDir "textures") -Recurse
Copy-Item (Join-Path $root "icons") (Join-Path $tempDir "icons") -Recurse

Compress-Archive -Path (Join-Path $tempDir "*") -DestinationPath $zipPath
Remove-Item $tempDir -Recurse -Force

Write-Host "Built $zipPath"
