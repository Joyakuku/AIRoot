param(
 [string]$Python='python',
 [string]$Exe=(Join-Path $PSScriptRoot '..\target\debug\airoot.exe'),
 [string]$Workspace=(Join-Path $PSScriptRoot '..\work'),
 [string]$Result=(Join-Path $PSScriptRoot '..\work\acceptance-results.json')
)
$ErrorActionPreference='Stop'
$repoRoot=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Workspace=[System.IO.Path]::GetFullPath($Workspace)
$Result=[System.IO.Path]::GetFullPath($Result)
New-Item -ItemType Directory -Path $Workspace -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $Result) -Force | Out-Null
$testRoot=Join-Path $Workspace ('acceptance-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$data=Join-Path $testRoot 'data'
$planFile=Join-Path $testRoot 'plan.json'
$results=[Collections.Generic.List[string]]::new()
function Invoke-AIRoot([string[]]$Arguments){$output=& $Exe --data-root $data --json @Arguments;if($LASTEXITCODE -ne 0){throw "CLI failed for '$($Arguments -join ' ')': $output"};try{return ($output|ConvertFrom-Json -ErrorAction Stop).data}catch{throw "CLI returned invalid JSON for '$($Arguments -join ' ')': $output"}}
function Wait-Operation([string]$Id){$limit=[DateTime]::UtcNow.AddSeconds(60);do{$op=Invoke-AIRoot @('operation','show',$Id);if($op.status -ne 'executing'){return $op};Start-Sleep -Milliseconds 100}while([DateTime]::UtcNow -lt $limit);throw 'Operation timeout'}
function Write-JsonFile($Path,$Value){[System.IO.File]::WriteAllText($Path,($Value|ConvertTo-Json -Depth 12),[System.Text.UTF8Encoding]::new($false))}
function New-Plan($InputObject){Write-JsonFile $planFile $InputObject;return Invoke-AIRoot @('operation','plan','--file',$planFile)}
function Delete-Resource($Resource){$plan=New-Plan @{action='delete';resource_id=$Resource.id};$null=Invoke-AIRoot @('operation','apply',$plan.id,'--confirm',('delete:'+$Resource.id),'--confirm','external-use-reviewed');$op=Wait-Operation $plan.id;if($op.status -ne 'completed'){throw ($op|ConvertTo-Json -Depth 12)}}
try {
 $null=Invoke-AIRoot @('config','set','--env-root',(Join-Path $testRoot 'env space'),'--tools-root',(Join-Path $testRoot 'tools space'),'--initialize')
 $plan=New-Plan @{action='create';kind='venv';name='中文-script';project='integration';manager=$Python}
 $null=Invoke-AIRoot @('operation','apply',$plan.id)
 $op=Wait-Operation $plan.id
 if($op.status -ne 'completed' -or $op.verification_status -ne 'succeeded'){throw ($op|ConvertTo-Json -Depth 12)}
 $envResource=$op.resource
 if($envResource.metadata.dependency_status -ne 'verified'){throw 'Dependency discovery failed'}
 $script=Join-Path $testRoot 'tools space\reusable.py'; 'print("reusable-ok")'|Set-Content -LiteralPath $script -Encoding utf8
 $resource=@{id='ignored';class='tool';kind='script';name='reusable';path=$script;entry=$null;version=$null;parent_id=$envResource.id;scope='host';status='unverified';projects=@('integration');dependencies=@();metadata=@{reusable=$true};updated_at=''}
 Write-JsonFile $planFile $resource
 $registered=@(Invoke-AIRoot @('registry','add','--file',$planFile))[0]
 $null=Invoke-AIRoot @('registry','verify',$registered.id)
 $run=Invoke-AIRoot @('resource','run',$registered.id,'--project','script-use')
 if($run.stdout.Trim() -ne 'reusable-ok'){throw 'Reusable script execution failed'}
 $results.Add('Reusable script verification, interpreter relation and protected run')
 $mcp=Join-Path $testRoot 'tools space\sample_mcp.py'
 @"
import json,sys
for line in sys.stdin:
    request=json.loads(line)
    if request.get('method')=='initialize':
        print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'protocolVersion':'2024-11-05','serverInfo':{'name':'AIRoot-test','version':'1'},'capabilities':{}}}),flush=True)
"@ | Set-Content -LiteralPath $mcp -Encoding utf8
 $resource.kind='mcp-connector';$resource.name='sample-connection';$resource.path=$mcp;$resource.metadata=@{transport='stdio';args=@($mcp)}
 Write-JsonFile $planFile $resource
 $connector=@(Invoke-AIRoot @('registry','add','--file',$planFile))[0]
 $connector=Invoke-AIRoot @('registry','verify',$connector.id)
 if($connector.status -ne 'available' -or $connector.metadata.connection.server_name -ne 'AIRoot-test'){throw 'MCP handshake failed'}
 $results.Add('Installed local stdio MCP initialize handshake; test server only')
 $scan=Invoke-AIRoot @('scan','start','--root',(Join-Path $testRoot 'env space'))
 $limit=[DateTime]::UtcNow.AddSeconds(30)
 do{$scan=Invoke-AIRoot @('scan','status',$scan.id);if($scan.status -ne 'running'){break};Start-Sleep -Milliseconds 100}while([DateTime]::UtcNow -lt $limit)
 $provider=@($scan.providers|Where-Object {$_.name -eq 'es'})[0]
 if($provider.status -ne 'queried' -or $provider.candidates -lt 1){throw ($scan|ConvertTo-Json -Depth 12)}
 $results.Add('Embedded Everything indexes Chinese/spaced roots and returns environment candidates')
 $archive=Join-Path $repoRoot 'work\downloads\fd.zip'
 $hash=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
 $plan=New-Plan @{action='install-portable';class='tool';kind='cli';name='fd';archive=$archive;sha256=$hash;source='https://github.com/sharkdp/fd/releases/download/v10.5.0/fd-v10.5.0-x86_64-pc-windows-msvc.zip';project='integration'}
 $faultScript=Join-Path $testRoot 'registry-fault.py'
 [System.IO.File]::WriteAllText($faultScript,@'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    db.execute("CREATE TRIGGER fail_registration BEFORE INSERT ON resources BEGIN SELECT RAISE(FAIL, 'injected registration failure'); END")
    db.commit()
'@,[System.Text.UTF8Encoding]::new($false))
 & $Python $faultScript (Join-Path $data 'registry.sqlite3')
 if($LASTEXITCODE -ne 0){throw 'Fault injection failed'}
 $null=Invoke-AIRoot @('operation','apply',$plan.id,'--confirm','source-reviewed')
 $op=Wait-Operation $plan.id
 if($op.status -ne 'registration_failed' -or $op.execution_status -ne 'succeeded' -or $op.verification_status -ne 'succeeded' -or -not (Test-Path -LiteralPath $op.resource.entry)){throw ($op|ConvertTo-Json -Depth 12)}
 $repeated=& $Exe --data-root $data --json operation apply $plan.id --confirm source-reviewed
 if($LASTEXITCODE -eq 0){throw 'Repeated installation was not blocked'}
 $faultScript=Join-Path $testRoot 'registry-fault.py'
 [System.IO.File]::WriteAllText($faultScript,@'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    db.execute("DROP TRIGGER fail_registration")
    db.commit()
'@,[System.Text.UTF8Encoding]::new($false))
 & $Python $faultScript (Join-Path $data 'registry.sqlite3')
 $null=Invoke-AIRoot @('operation','recover',$op.id)
 $limit=[DateTime]::UtcNow.AddSeconds(60)
 do { $op=Invoke-AIRoot @('operation','show',$op.id); if($op.reliability.recovery.status -ne 'running'){break}; Start-Sleep -Milliseconds 100 } while([DateTime]::UtcNow -lt $limit)
 if($op.status -ne 'completed' -or $op.attempts -ne 1){throw ($op|ConvertTo-Json -Depth 12)}
 $tool=$op.resource
 $run=Invoke-AIRoot @('resource','run',$tool.id,'--','--version')
 if($run.stdout -notmatch 'fd 10.5.0'){throw 'Portable tool execution failed'}
 $results.Add('Real pinned ZIP extraction, verification, registration failure preserves resource, repeat blocked, registration repair succeeds')
 Delete-Resource $tool
 Delete-Resource $envResource
 if(-not (Test-Path -LiteralPath $script)){throw 'External script was deleted with environment'}
 $remaining=Invoke-AIRoot @('tool','show',$registered.id)
 if($remaining.parent_id -or $remaining.status -ne 'unverified'){throw 'External script dependency was not invalidated'}
 $results.Add('Portable deletion and dependent external scripts retained/invalidation synchronized')
 $null=Invoke-AIRoot @('components','stop')
 $null=Invoke-AIRoot @('worker-stop')
 @{passed=$results;test_root=$testRoot;at=(Get-Date -Format o)}|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $Result -Encoding utf8
 $results
} catch {Write-Error "Acceptance failed in ${testRoot}: $_";exit 1}
