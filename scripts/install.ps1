param([string]$InstallRoot=(Join-Path $env:LOCALAPPDATA 'Programs\NaraeHangulCLI'),[string]$SkillsDirectory,[string]$BackupRoot)
$ErrorActionPreference='Stop'
$sourceRoot=[IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$InstallRoot=[IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
if($InstallRoot -eq [IO.Path]::GetPathRoot($InstallRoot).TrimEnd('\')){throw 'Choose an application directory, not a drive root.'}
if(-not [Environment]::Is64BitOperatingSystem){throw 'Windows x64 is required.'}
$env:PYTHONUTF8='1';$env:PYTHONNOUSERSITE='1'
foreach($item in @('PYTHONHOME','PYTHONPATH','PYTHONUSERBASE')){[Environment]::SetEnvironmentVariable($item,$null,'Process')}
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
$env:TEMP=Join-Path $InstallRoot 'cache';$env:TMP=$env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$engineFolder=Get-ChildItem -LiteralPath $sourceRoot -Directory | Where-Object {$_.Name -like '1_*'} | Select-Object -First 1
$skillSource=if($engineFolder){Join-Path $engineFolder.FullName 'narae-hangul'}else{Join-Path $sourceRoot 'skill'}
if(-not (Test-Path -LiteralPath (Join-Path $skillSource 'SKILL.md'))){throw 'Source skill is missing.'}
$skill=Join-Path $InstallRoot 'skill';New-Item -ItemType Directory -Path $skill -Force | Out-Null
if([IO.Path]::GetFullPath($skillSource) -ne [IO.Path]::GetFullPath($skill)){
    Get-ChildItem -LiteralPath $skillSource -Force | Where-Object {$_.Name -notin @('runtime.json','__pycache__')} | Copy-Item -Destination $skill -Recurse -Force
}
$pythonRoot=& (Join-Path $PSScriptRoot 'bootstrap-python.ps1') -AppRoot $InstallRoot
$python=Join-Path $pythonRoot 'python.exe'
& $python -I -m pip install --only-binary=:all: --disable-pip-version-check --no-warn-script-location -r (Join-Path $skill 'scripts\engine\requirements.txt')
if($LASTEXITCODE -ne 0){throw 'CLI dependencies failed to install.'}
@{python=$python} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $skill 'runtime.json') -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $sourceRoot 'narae-hangul.cmd') -Destination $InstallRoot -Force
Copy-Item -LiteralPath (Join-Path $sourceRoot 'LICENSE') -Destination $InstallRoot -Force
$arguments=@('--source',$skill,'--python',$python)
if($SkillsDirectory){$arguments+=@('--skills-directory',$SkillsDirectory)}
if($BackupRoot){$arguments+=@('--backup-root',$BackupRoot)}
& $python -I -X utf8 (Join-Path $PSScriptRoot 'install-skill.py') @arguments
if($LASTEXITCODE -ne 0){throw 'Skill installation failed.'}
& $python -I -X utf8 (Join-Path $skill 'scripts\narae.py') doctor
if($LASTEXITCODE -ne 0){throw 'CLI doctor failed.'}
Write-Output "Installed independent Narae Hangul CLI: $InstallRoot"
