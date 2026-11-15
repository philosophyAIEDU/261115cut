# Builds build\installer\SophiCut-Setup.exe
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-Version 1.2.0] [-Python py]
# Needs: Python 3.10-3.13, Inno Setup 6 (winget install JRSoftware.InnoSetup), internet.
param(
  [string]$Version = "",
  [string]$Python = "python"
)
$ErrorActionPreference = "Continue"   # native tools write progress to stderr; check exit codes instead
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

$Root = Split-Path -Parent $PSScriptRoot
$Build = Join-Path $Root "build"
Set-Location $Root
New-Item -ItemType Directory -Force $Build | Out-Null

if (-not $Version) {
  $Version = (Select-String -Path "sophicut\__init__.py" -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
}
Write-Host "== SophiCut $Version =="

# 1) clean build environment
$venv = Join-Path $Build "venv"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) { & $Python -m venv $venv; Check "venv" }
& $py -m pip install --upgrade pip --quiet; Check "pip upgrade"
& $py -m pip install -r requirements.txt pyinstaller --quiet; Check "pip install"

# 2) icon
& $py packaging\make_icon.py (Join-Path $Build "sophicut.ico"); Check "icon"

# 3) app bundle
$dist = Join-Path $Build "dist"
& $py -m PyInstaller --noconfirm --clean --windowed --name SophiCut `
  --icon (Join-Path $Build "sophicut.ico") `
  --distpath $dist --workpath (Join-Path $Build "work") --specpath $Build --paths $Root `
  --add-data "$Root\sophicut\static;sophicut\static" `
  --add-data "$Root\sophicut\fonts;sophicut\fonts" `
  --hidden-import sophicut.app `
  --collect-all ctranslate2 --collect-all faster_whisper --collect-all onnxruntime --collect-all av `
  (Join-Path $Root "packaging\entry.py")
Check "PyInstaller"
$app = Join-Path $dist "SophiCut"

# 4) ffmpeg (BtbN GPL shared build, newest release branch)
$ffzip = Join-Path $Build "ffmpeg.zip"
if (-not (Test-Path $ffzip)) {
  $headers = @{ "User-Agent" = "sophicut-build" }
  if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  $rel = Invoke-RestMethod "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest" -Headers $headers
  $asset = $rel.assets | Where-Object { $_.name -match '^ffmpeg-n\d+\.\d+-latest-win64-gpl-shared-\d+\.\d+\.zip$' } |
    Sort-Object { [version]([regex]::Match($_.name, 'n(\d+\.\d+)').Groups[1].Value) } -Descending | Select-Object -First 1
  if (-not $asset) { throw "ffmpeg build not found" }
  Write-Host "ffmpeg: $($asset.name)"
  $ProgressPreference = "SilentlyContinue"
  Invoke-WebRequest $asset.browser_download_url -OutFile $ffzip -UseBasicParsing
}
$ffx = Join-Path $Build "ffmpeg_x"
if (Test-Path $ffx) { Remove-Item -Recurse -Force $ffx }
Expand-Archive $ffzip $ffx
$ffroot = Get-ChildItem $ffx -Directory | Select-Object -First 1
$ffdst = Join-Path $app "ffmpeg"
New-Item -ItemType Directory -Force $ffdst | Out-Null
Copy-Item (Join-Path $ffroot.FullName "bin\ffmpeg.exe"), (Join-Path $ffroot.FullName "bin\ffprobe.exe") $ffdst
Copy-Item (Join-Path $ffroot.FullName "bin\*.dll") $ffdst
Copy-Item (Join-Path $ffroot.FullName "LICENSE.txt") (Join-Path $ffdst "FFMPEG-LICENSE.txt")

# 5) installer
$iscc = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
& $iscc "/DAppVersion=$Version" /Q (Join-Path $Root "packaging\installer.iss"); Check "Inno Setup"

$out = Join-Path $Build "installer\SophiCut-Setup.exe"
Write-Host ("== done: {0} ({1:N0} MB) ==" -f $out, ((Get-Item $out).Length / 1MB))
