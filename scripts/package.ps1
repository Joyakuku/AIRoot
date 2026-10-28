param([string]$Python='python',[string]$Cargo='cargo')
$ErrorActionPreference='Stop'
$repo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $repo
try {
 if(-not (Test-Path -LiteralPath (Join-Path $repo 'components/manifest.json'))){throw 'Run scripts/fetch-components.ps1 first'}
 & $Cargo test --locked --quiet
 if($LASTEXITCODE -ne 0){throw 'Tests failed'}
 & $Cargo build --locked --release --bin airoot
 if($LASTEXITCODE -ne 0){throw 'Release build failed'}
 $build=Join-Path $repo ('work/package-'+[Guid]::NewGuid().ToString('N'))
 New-Item -ItemType Directory -Path $build|Out-Null
 $payload=& $Python (Join-Path $PSScriptRoot 'package_payload.py') --stage (Join-Path $build 'payload') --cargo $Cargo
 if($LASTEXITCODE -ne 0){throw 'Payload or license checks failed'}
 $previousPayload=$env:AIROOT_SETUP_PAYLOAD
 try {
  $env:AIROOT_SETUP_PAYLOAD=$payload
  & $Cargo build --locked --release --bin airoot-setup
  if($LASTEXITCODE -ne 0){throw 'Installer build failed'}
 } finally {$env:AIROOT_SETUP_PAYLOAD=$previousPayload}
 $dist=Join-Path $repo 'dist';New-Item -ItemType Directory -Path $dist -Force|Out-Null
 $setup=Join-Path $dist 'AIRoot-0.1.1-preview-setup.exe'
 Copy-Item -LiteralPath (Join-Path $repo 'target/release/airoot-setup.exe') -Destination $setup -Force
 $hash=(Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()
 "$hash  AIRoot-0.1.1-preview-setup.exe" | Set-Content -LiteralPath (Join-Path $dist 'SHA256SUMS.txt') -Encoding ascii
 @{setup=$setup;payload=$payload;sha256=$hash;channel='preview';at=(Get-Date -Format o)}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $repo 'work/package-results.json') -Encoding utf8
 Write-Output "Built preview installer: $setup"
} finally {Pop-Location}
