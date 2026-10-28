param([string]$Python='python',[string]$Exe=(Join-Path $PSScriptRoot '..\target\debug\airoot.exe'))
$ErrorActionPreference='Stop'
$workspace=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$testRoot=Join-Path $workspace ('work\smoke-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$data=Join-Path $testRoot 'data'
function Invoke-AIRoot([string[]]$Arguments){$output=& $Exe --data-root $data --json @Arguments; if($LASTEXITCODE -ne 0){throw "CLI failed: $output"}; $response=$output|ConvertFrom-Json;if(-not $response.ok){throw $output};return $response.data}
function Wait-Operation([string]$Id){$limit=[DateTime]::UtcNow.AddSeconds(60);do{$op=Invoke-AIRoot @('operation','show',$Id);if($op.status -ne 'executing'){return $op};Start-Sleep -Milliseconds 100}while([DateTime]::UtcNow -lt $limit);throw 'Operation timeout'}
$results=[Collections.Generic.List[string]]::new()
try {
 $null=Invoke-AIRoot @('status')
 $null=Invoke-AIRoot @('config','set','--env-root',(Join-Path $testRoot 'env'),'--tools-root',(Join-Path $testRoot 'tools'),'--initialize')
 $planFile=Join-Path $testRoot 'create.json'
 @{action='create';kind='venv';name='中文-demo';project='sample-project';manager=$Python}|ConvertTo-Json|Set-Content -LiteralPath $planFile -Encoding utf8
 $plan=Invoke-AIRoot @('operation','plan','--file',$planFile)
 $null=Invoke-AIRoot @('operation','apply',$plan.id)
 $op=Wait-Operation $plan.id
 if($op.status -ne 'completed'){throw ($op|ConvertTo-Json -Depth 12)}
 $results.Add('Real venv create + verification + registration')
 $resources=Invoke-AIRoot @('env','list')
 if(@($resources).Count -ne 1){throw 'Registry count mismatch'}
 $resource=@($resources)[0]
 $run=Invoke-AIRoot @('resource','run',$resource.id,'--project','actual-use','--','-c','print(6*7)')
 if($run.stdout.Trim() -ne '42'){throw 'Python output mismatch'}
 $project=Invoke-AIRoot @('project','show','actual-use')
 if(@($project.resources).Count -ne 1){throw 'Actual-use project relation missing'}
 $results.Add('Protected resource run and project association')
 $null=Invoke-AIRoot @('registry','ignore',$resource.id)
 $scan=Invoke-AIRoot @('scan','start','--root',(Join-Path $testRoot 'env'))
 $limit=[DateTime]::UtcNow.AddSeconds(30)
 do{$scan=Invoke-AIRoot @('scan','status',$scan.id);if($scan.status -ne 'running'){break};Start-Sleep -Milliseconds 100}while([DateTime]::UtcNow -lt $limit)
 if(@(Invoke-AIRoot @('env','list')).Count -ne 0){throw 'Ignored environment was rediscovered'}
 $null=Invoke-AIRoot @('registry','restore',$resource.id)
 $null=Invoke-AIRoot @('registry','verify',$resource.id)
 $results.Add('Scoped scan, ignore and restore')
 @{action='delete';resource_id=$resource.id}|ConvertTo-Json|Set-Content -LiteralPath $planFile -Encoding utf8
 $plan=Invoke-AIRoot @('operation','plan','--file',$planFile)
 $unconfirmed=& $Exe --data-root $data --json operation apply $plan.id
 if($LASTEXITCODE -eq 0){throw 'Deletion without specific confirmation unexpectedly succeeded'}
 if(-not (Test-Path -LiteralPath $resource.path)){throw 'Unconfirmed deletion changed filesystem'}
 $null=Invoke-AIRoot @('operation','apply',$plan.id,'--confirm',('delete:'+$resource.id),'--confirm','external-use-reviewed')
 $op=Wait-Operation $plan.id
 if($op.status -ne 'completed'){throw ($op|ConvertTo-Json -Depth 12)}
 if(Test-Path -LiteralPath $resource.path){throw 'Deleted environment still exists'}
 $results.Add('Exact deletion confirmation, filesystem deletion and registry synchronization')
 $null=Invoke-AIRoot @('components','stop')
 $null=Invoke-AIRoot @('worker-stop')
 @{passed=$results;test_root=$testRoot;at=(Get-Date -Format o)}|ConvertTo-Json -Depth 6|Set-Content (Join-Path $workspace 'work/smoke-results.json') -Encoding utf8
 $results
} catch {Write-Error "Smoke failed in ${testRoot}: $_";exit 1}
