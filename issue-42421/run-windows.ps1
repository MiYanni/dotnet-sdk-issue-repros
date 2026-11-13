$ErrorActionPreference = 'Stop'
$root = Join-Path $env:GITHUB_WORKSPACE 'issue-42421'
Set-Location $root
$env:DOTNET_CLI_HOME = Join-Path $env:RUNNER_TEMP 'issue42421-cli-home'
$env:NUGET_PACKAGES = Join-Path $env:RUNNER_TEMP 'issue42421-packages'
$env:NUGET_HTTP_CACHE_PATH = Join-Path $env:RUNNER_TEMP 'issue42421-http'
$env:NUGET_PLUGINS_CACHE_PATH = Join-Path $env:RUNNER_TEMP 'issue42421-plugins'
$env:DOTNET_ADD_GLOBAL_TOOLS_TO_PATH = '0'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$dotnet = (Get-Command dotnet).Source
$linuxRoot = (& wsl.exe --exec wslpath -a $root).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve shared WSL path' }
$script = "$linuxRoot/linux-step.sh"
$privateRoot = Join-Path $root 'private-evidence'
New-Item -ItemType Directory -Path $privateRoot -Force | Out-Null
function Run-Wsl([string] $Mode, [string] $Label, [string] $Artifacts = '') {
    & wsl.exe --exec bash $script $Mode $linuxRoot $Label $Artifacts
    if ($LASTEXITCODE -ne 0) { throw "WSL harness failed for $Label" }
}
function Start-WindowsCommand([string] $Exe, [string[]] $Arguments, [string] $Label) {
    $directory = Join-Path $privateRoot $Label
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $process = Start-Process -FilePath $Exe -ArgumentList $Arguments -WorkingDirectory $root -PassThru `
        -RedirectStandardOutput "$directory\stdout.raw" -RedirectStandardError "$directory\stderr.raw"
    [pscustomobject]@{Process=$process;Directory=$directory;Label=$Label;Executable=$Exe;Arguments=$Arguments;StartedUtc=[DateTime]::UtcNow}
}
function Finish-WindowsCommand($Invocation) {
    $Invocation.Process.WaitForExit()
    $Invocation.Process.Refresh()
    $record = @{
        host='Windows'; pid=$Invocation.Process.Id; startedUtc=$Invocation.StartedUtc
        finishedUtc=[DateTime]::UtcNow; exitCode=$Invocation.Process.ExitCode
        executable=$Invocation.Executable; arguments=$Invocation.Arguments
        fallbackEnvironment=$env:NUGET_FALLBACK_PACKAGES
    }
    $record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$($Invocation.Directory)\invocation.json"
    Get-Content "$($Invocation.Directory)\stdout.raw","$($Invocation.Directory)\stderr.raw" |
        Where-Object { $_ -match 'Writing assets file|Generating MSBuild file|Persisting dg|error |fallback package folder|Captured consumer|MSBuild version' } |
        Set-Content -LiteralPath "$($Invocation.Directory)\diagnostics.txt"
    python snapshot.py $root $Invocation.Directory
    Write-Host "$($Invocation.Label) exit=$($record.exitCode) pid=$($record.pid)"
    return $record.exitCode
}
function Run-WindowsRestore([string] $Label, [string] $Fallback = '', [string] $Artifacts = '', [string] $Executable = $dotnet) {
    $directory = Join-Path $privateRoot $Label
    $arguments = @('restore','App\App.csproj','--configfile','NuGet.config','--force','-v:diag','-m:1','-nr:false',"-bl:$directory\restore.binlog")
    if ($Executable -ne $dotnet) {
        $arguments = @('App\App.csproj','-t:Restore','-p:RestoreConfigFile=NuGet.config','-p:RestoreForce=true','-v:diag','-m:1','-nr:false',"-bl:$directory\restore.binlog")
    }
    if ($Fallback) { $arguments += "-p:RestoreFallbackFolders=$Fallback" }
    if ($Artifacts) { $arguments += "-p:ArtifactsPath=$Artifacts" }
    Finish-WindowsCommand (Start-WindowsCommand $Executable $arguments $Label)
}
dotnet --info | Set-Content "$privateRoot\windows-info.txt"
Run-Wsl prepare wsl-prepare
Run-Wsl restore wsl-clean
Run-Wsl probe wsl-clean
$code = Run-WindowsRestore windows-default
if ($code -ne 0) { throw 'Default Windows restore failed' }
Run-Wsl probe wsl-after-windows-default
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$msbuild = & $vswhere -latest -products '*' -requires Microsoft.Component.MSBuild -find MSBuild\**\Bin\MSBuild.exe | Select-Object -First 1
if ($msbuild) {
    $code = Run-WindowsRestore windows-vs-msbuild '' '' $msbuild
    Run-Wsl probe wsl-after-vs-msbuild
} else {
    'No Visual Studio MSBuild was found; natural VS default case blocked.' | Set-Content "$privateRoot\vs-unavailable.txt"
}
# Populate a real fallback cache through restore, rather than manufacturing a missing directory.
$fallback = Join-Path $env:RUNNER_TEMP 'issue42421-real-fallback-packages'
$originalPackages = $env:NUGET_PACKAGES
$env:NUGET_PACKAGES = $fallback
$code = Run-WindowsRestore windows-populate-fallback
$env:NUGET_PACKAGES = $originalPackages
if ($code -ne 0) { throw 'Could not populate the valid Windows fallback package cache' }
$code = Run-WindowsRestore windows-configured-fallback $fallback
if ($code -ne 0) { throw 'Valid configured Windows fallback restore failed' }
Run-Wsl probe wsl-after-configured-windows
Run-Wsl restore wsl-repaired
Run-Wsl probe wsl-repaired
# Deliberately schedule the real Windows restore in the restore-to-consume interval.
$code = Run-WindowsRestore windows-between-restore-and-consume $fallback
if ($code -ne 0) { throw 'Scheduled Windows restore failed' }
Run-Wsl probe wsl-scheduled-overwrite
for ($i = 1; $i -le 3; $i++) {
    $label = "windows-concurrent-$i"
    $directory = Join-Path $privateRoot $label
    $args = @('restore','App\App.csproj','--configfile','NuGet.config','--force','-v:diag','-m:1','-nr:false',"-p:RestoreFallbackFolders=$fallback","-bl:$directory\restore.binlog")
    $writer = Start-WindowsCommand $dotnet $args $label
    Run-Wsl restore "wsl-concurrent-$i"
    Run-Wsl probe "wsl-concurrent-$i"
    $null = Finish-WindowsCommand $writer
}
$windowsArtifacts = Join-Path $root 'artifacts-windows'
$linuxArtifacts = "$linuxRoot/artifacts-linux"
Run-Wsl restore wsl-isolated $linuxArtifacts
$code = Run-WindowsRestore windows-isolated $fallback $windowsArtifacts
if ($code -ne 0) { throw 'Isolated Windows restore failed' }
Run-Wsl probe wsl-isolated $linuxArtifacts
python sanitize.py private-evidence evidence
if ($LASTEXITCODE -ne 0) { throw 'Evidence sanitization failed' }
python check-results.py evidence windows
if ($LASTEXITCODE -ne 0) { throw 'Required Windows/WSL controls were not established' }
