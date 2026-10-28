param([string]$Destination=(Join-Path $PSScriptRoot '..\components'),[string]$VCRuntimeSource)
$ErrorActionPreference='Stop'
$repo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$lock=Get-Content (Join-Path $repo 'packaging\components.lock.json') -Raw|ConvertFrom-Json
$stage=Join-Path $repo ('work\component-fetch-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage,$Destination -Force|Out-Null
foreach($item in $lock.components){
 if($item.acquisition -eq 'licensed-visual-studio-redist'){
  if(-not $VCRuntimeSource){throw 'PET runtime requires -VCRuntimeSource pointing to the licensed VS release redist file, not System32 or a DLL download site'}
  $binary=(Resolve-Path -LiteralPath $VCRuntimeSource).Path
  if($binary -notmatch '(?i)\\VC\\Redist\\MSVC\\[^\\]+\\x64\\Microsoft\.VC14[0-9]\.CRT\\vcruntime140\.dll$'){throw 'Runtime must come from the x64 Visual Studio release redist tree'}
  $signature=Get-AuthenticodeSignature -LiteralPath $binary
  if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'CN=Microsoft Corporation(?:,|$)'){throw 'Microsoft runtime signature verification failed'}
  if((Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash -ne $item.sha256){throw 'Pinned PET runtime checksum mismatch'}
  Copy-Item -LiteralPath $binary -Destination (Join-Path $Destination $item.file)
  continue
 }
 $archive=Join-Path $stage ($item.name+'.zip')
 Invoke-WebRequest -Uri $item.source -OutFile $archive
 if((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $item.archive_sha256){throw "Archive checksum mismatch: $($item.name)"}
 $extracted=Join-Path $stage $item.name
 Expand-Archive -LiteralPath $archive -DestinationPath $extracted
 $binary=Join-Path $extracted $item.archive_member
 if((Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash -ne $item.sha256){throw "Binary checksum mismatch: $($item.name)"}
 Copy-Item -LiteralPath $binary -Destination (Join-Path $Destination $item.file)
}
Copy-Item -LiteralPath (Join-Path $repo 'packaging\components.lock.json') -Destination (Join-Path $Destination 'manifest.json')
Copy-Item -LiteralPath (Join-Path $repo 'packaging\licenses') -Destination $Destination -Recurse -Force
Write-Output "Verified components: $Destination. Download materials retained at $stage."
