param([Parameter(Mandatory=$true)][string]$AppRoot)
$ErrorActionPreference='Stop'
$AppRoot=[IO.Path]::GetFullPath($AppRoot)
$manifest=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime-downloads.json') -Encoding UTF8 -Raw | ConvertFrom-Json
$cache=Join-Path $AppRoot 'cache\downloads';$runtime=Join-Path $AppRoot 'runtime'
New-Item -ItemType Directory -Path $cache,$runtime -Force | Out-Null
[Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
function Get-VerifiedArchive($Spec,$Name) {
    $file=Join-Path $cache $Name
    if((Test-Path -LiteralPath $file) -and (Get-FileHash -LiteralPath $file).Hash -eq $Spec.sha256){return $file}
    $part=$file+'.part'
    for($attempt=1;$attempt -le 3;$attempt++){
        try {
            Invoke-WebRequest -Uri $Spec.url -OutFile $part -UseBasicParsing
            if((Get-FileHash -LiteralPath $part).Hash -ne $Spec.sha256){throw '다운로드 파일의 SHA256이 일치하지 않습니다.'}
            Move-Item -LiteralPath $part -Destination $file -Force
            return $file
        } catch {if($attempt -eq 3){throw};Start-Sleep -Seconds 2}
    }
}
function Expand-Runtime($Spec,$Name,$Executable) {
    $folder=Join-Path $runtime $Name;$marker=Join-Path $folder 'narae-runtime.json'
    if((Test-Path -LiteralPath $marker) -and (Test-Path -LiteralPath (Join-Path $folder $Executable))){
        $previous=Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
        if($previous.sha256 -eq $Spec.sha256){return $folder}
    }
    $archive=Get-VerifiedArchive $Spec ($Name+'-'+$Spec.version+'.zip')
    $stage=Join-Path $runtime ('stage-'+[Guid]::NewGuid().ToString('N'));New-Item -ItemType Directory -Path $stage | Out-Null
    try {
        Expand-Archive -LiteralPath $archive -DestinationPath $stage
        $expanded=$stage
        if($Spec.PSObject.Properties.Name -contains 'archiveFolder'){$expanded=Join-Path $stage $Spec.archiveFolder}
        if(-not (Test-Path -LiteralPath (Join-Path $expanded $Executable))){throw ($Name+' 실행 파일이 없습니다.')}
        if(Test-Path -LiteralPath $folder){
            $old=Join-Path $runtime ($Name+'-previous-'+[Guid]::NewGuid().ToString('N'))
            foreach($target in @($folder,$old)){if(-not [IO.Path]::GetFullPath($target).StartsWith($runtime+'\',[StringComparison]::OrdinalIgnoreCase)){throw '런타임 경로 확인 실패'}}
            Move-Item -LiteralPath $folder -Destination $old
        }
        Move-Item -LiteralPath $expanded -Destination $folder
        @{version=$Spec.version;sha256=$Spec.sha256;url=$Spec.url} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $folder 'narae-runtime.json') -Encoding UTF8
        return $folder
    } finally {
        $resolved=[IO.Path]::GetFullPath($stage)
        if($resolved.StartsWith($runtime+'\stage-',[StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $resolved)){Remove-Item -LiteralPath $resolved -Recurse -Force}
    }
}
$pythonRoot=Expand-Runtime $manifest.python 'python' 'python.exe'
& (Join-Path $pythonRoot 'python.exe') -I -c "import sys;assert sys.version_info[:3]==(3,13,17)" | Out-Host
if($LASTEXITCODE -ne 0){throw 'Python runtime validation failed'}
$pythonRoot
