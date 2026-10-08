param([string]$Python = (Join-Path $PSScriptRoot '../../.venv/Scripts/python.exe'))
$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
& $Python -m PyInstaller --noconfirm --distpath (Join-Path $project 'release') --workpath (Join-Path $project 'build') (Join-Path $PSScriptRoot 'ChairsideCompare.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
$delivery = Join-Path $project 'release/ChairsideCompare'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '使用说明.txt') -Destination $delivery -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'third_party') -Destination $delivery -Recurse -Force
Write-Host 'Executable built. Validate --package-check before distributing.'
