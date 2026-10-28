param(
    [string]$Cargo = (Join-Path $HOME '.cargo\bin\cargo.exe')
)

$ErrorActionPreference = 'Stop'
$plugin = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$repo = (Resolve-Path (Join-Path $plugin '..\..')).Path
$runtime = Join-Path $plugin 'runtime'
if (-not (Test-Path -LiteralPath $Cargo -PathType Leaf)) {
    $cargoCommand = Get-Command cargo -ErrorAction SilentlyContinue
    if ($cargoCommand) { $Cargo = $cargoCommand.Source }
}
if (-not (Test-Path -LiteralPath $Cargo -PathType Leaf)) {
    throw 'Rust Cargo was not found. Install Rust or pass -Cargo with its executable path.'
}

function Get-Sha256([string]$Path) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try { return [System.BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
        finally { $stream.Dispose() }
    } finally {
        $algorithm.Dispose()
    }
}

function Test-RuntimeBundle([string]$Path) {
    $manifestPath = Join-Path $Path 'components\manifest.json'
    $exePath = Join-Path $Path 'airoot.exe'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $exePath -PathType Leaf)) { return $false }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    foreach ($component in $manifest.components) {
        $file = Join-Path (Join-Path $Path 'components') $component.file
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or
            (Get-Sha256 $file) -ne $component.sha256.ToLowerInvariant()) { return $false }
    }
    return $true
}

if (Test-RuntimeBundle $runtime) { exit 0 }

$componentManifest = Join-Path $repo 'components\manifest.json'
if (-not (Test-Path -LiteralPath $componentManifest -PathType Leaf)) {
    $fetch = Join-Path $repo 'scripts\fetch-components.ps1'
    if ($env:AIROOT_VCRUNTIME_SOURCE) {
        & $fetch -Destination (Join-Path $repo 'components') -VCRuntimeSource $env:AIROOT_VCRUNTIME_SOURCE
    } else {
        & $fetch -Destination (Join-Path $repo 'components')
    }
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'AIRoot component acquisition failed' }
}

Push-Location $repo
$previousTargetDir = $env:CARGO_TARGET_DIR
try {
    $env:CARGO_TARGET_DIR = Join-Path $repo 'target\dsh-plugin'
    & $Cargo build --locked --release --bin airoot
    if ($LASTEXITCODE -ne 0) { throw 'AIRoot release build failed' }
} finally {
    $env:CARGO_TARGET_DIR = $previousTargetDir
    Pop-Location
}

if (-not (Test-Path -LiteralPath $componentManifest -PathType Leaf)) {
    throw 'Component manifest was not produced; provide a licensed Visual C++ runtime source when required.'
}

$stage = Join-Path $plugin ('runtime-stage-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    Copy-Item -LiteralPath (Join-Path $repo 'target\dsh-plugin\release\airoot.exe') -Destination (Join-Path $stage 'airoot.exe')
    $componentDestination = Join-Path $stage 'components'
    New-Item -ItemType Directory -Path $componentDestination | Out-Null
    $manifest = Get-Content -LiteralPath $componentManifest -Raw | ConvertFrom-Json
    foreach ($component in $manifest.components) {
        $source = Join-Path (Join-Path $repo 'components') $component.file
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Missing component payload: $($component.file)"
        }
        if ((Get-Sha256 $source) -ne $component.sha256.ToLowerInvariant()) {
            throw "Component hash mismatch: $($component.name)"
        }
        Copy-Item -LiteralPath $source -Destination $componentDestination
    }
    Copy-Item -LiteralPath $componentManifest -Destination (Join-Path $componentDestination 'manifest.json')
    Copy-Item -LiteralPath (Join-Path $repo 'packaging\licenses') -Destination $componentDestination -Recurse
    if (-not (Test-RuntimeBundle $stage)) { throw 'Prepared runtime bundle did not pass hash verification' }
    if (Test-Path -LiteralPath $runtime) {
        throw "Runtime path exists but failed verification; inspect it before replacing: $runtime"
    }
    Move-Item -LiteralPath $stage -Destination $runtime
} finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
}

Write-Output "Prepared bundled AIRoot runtime: $runtime"
