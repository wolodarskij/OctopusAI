<#
.SYNOPSIS
    Builds the editor target of a project, unless an editor has the project open.

.DESCRIPTION
    Runs `Build.bat <Project>Editor Win64 Development -project=<uproject> -WaitMutex`. A project
    with no `Source\*Editor.Target.cs` builds the `UnrealEditor` target with its plugins.

    An editor that has the project open holds its DLLs, and a shared editor is never closed for a
    build. The script then builds nothing: it leaves `.octopus\build-pending` in place, or writes
    it, and exits 3. `start_editor.ps1` runs this script before it launches the editor whenever
    the marker exists, so the build happens the next time the editor is down.

    Install writes the marker when a pack replaced editor sources in the project (the
    AssetTextExport plugin of the blueprint pack). A build that succeeds removes it.

    The project and the engine come from, in this order:

      project  -ProjectDir, then OCTOPUS_PROJECT, then the nearest directory at or above the
               current directory that holds a *.uproject file
      engine   -EngineDir, then "engineDir" in <project>/.octopus/unreal.json, then UE_ENGINE_DIR

.PARAMETER IfPending
    Build only when `.octopus\build-pending` exists; exit 0 with nothing done otherwise.

.OUTPUTS
    Exit 0 BUILT or NOTHING-PENDING, 3 EDITOR-RUNNING, 4 BUILD-FAILED.

.EXAMPLE
    powershell -File build_editor.ps1 -ProjectDir C:\Work\MyGame
.EXAMPLE
    powershell -File build_editor.ps1 -ProjectDir C:\Work\MyGame -IfPending
#>
[CmdletBinding()]
param(
    [string] $ProjectDir,
    [string] $EngineDir,
    [string] $Configuration = 'Development',
    [switch] $IfPending
)

$ErrorActionPreference = 'Stop'

function Find-ProjectRoot([string] $start) {
    $d = Get-Item -LiteralPath $start
    while ($null -ne $d) {
        if (Get-ChildItem -LiteralPath $d.FullName -Filter *.uproject -File -ErrorAction SilentlyContinue) {
            return $d.FullName
        }
        $d = $d.Parent
    }
    return $null
}

if (-not $ProjectDir) { $ProjectDir = $env:OCTOPUS_PROJECT }
if (-not $ProjectDir) { $ProjectDir = Find-ProjectRoot (Get-Location).Path }
if (-not $ProjectDir -or -not (Test-Path -LiteralPath $ProjectDir -PathType Container)) {
    throw 'No project: pass -ProjectDir <path>, set OCTOPUS_PROJECT, or run inside a directory with a *.uproject file.'
}
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectDir).Path
$Pending = Join-Path $ProjectRoot '.octopus\build-pending'

if ($IfPending -and -not (Test-Path -LiteralPath $Pending)) {
    Write-Output 'NOTHING-PENDING: no .octopus\build-pending.'
    exit 0
}

$Settings = $null
$SettingsFile = Join-Path $ProjectRoot '.octopus\unreal.json'
if (Test-Path -LiteralPath $SettingsFile) {
    $Settings = Get-Content -LiteralPath $SettingsFile -Raw | ConvertFrom-Json
}
function Get-Setting([string] $name, $default) {
    if ($null -ne $Settings -and $null -ne $Settings.$name -and "$($Settings.$name)" -ne '') {
        return $Settings.$name
    }
    return $default
}

if (-not $EngineDir) { $EngineDir = Get-Setting 'engineDir' $env:UE_ENGINE_DIR }
if (-not $EngineDir) {
    throw 'No engine: pass -EngineDir <path>, set "engineDir" in .octopus\unreal.json, or set UE_ENGINE_DIR.'
}
$BuildBat = Join-Path $EngineDir 'Engine\Build\BatchFiles\Build.bat'
if (-not (Test-Path -LiteralPath $BuildBat)) { throw "Build.bat not found: $BuildBat" }

$UprojectName = Get-Setting 'uproject' ''
if ($UprojectName) {
    $ProjectFile = Join-Path $ProjectRoot $UprojectName
}
else {
    $first = Get-ChildItem -LiteralPath $ProjectRoot -Filter *.uproject -File | Sort-Object Name | Select-Object -First 1
    if (-not $first) { throw "No .uproject file in $ProjectRoot" }
    $ProjectFile = $first.FullName
}

function Write-Pending([string] $why) {
    if (-not (Test-Path -LiteralPath $Pending)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $Pending) | Out-Null
        ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $why) | Out-File -LiteralPath $Pending -Encoding utf8
    }
}

# --- guard: an editor that has this project open ------------------------------------
$needle = [IO.Path]::GetFullPath($ProjectFile).ToLowerInvariant()
$open = Get-CimInstance Win32_Process -Filter "Name like 'UnrealEditor%' or Name like 'UE4Editor%'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Replace('/', '\').ToLowerInvariant().Contains($needle) }
if ($open) {
    Write-Pending 'editor running'
    Write-Output ("EDITOR-RUNNING: PID {0} has {1} open. Nothing built; .octopus\build-pending waits for the editor to close." -f (($open.ProcessId) -join ', '), [IO.Path]::GetFileName($ProjectFile))
    exit 3
}

# --- build ---------------------------------------------------------------------------
$targetFile = Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'Source') -Filter '*Editor.Target.cs' -File -ErrorAction SilentlyContinue |
    Sort-Object Name | Select-Object -First 1
$Target = if ($targetFile) { $targetFile.Name -replace '\.Target\.cs$', '' } else { 'UnrealEditor' }

Write-Output ("BUILDING: {0} Win64 {1} -project={2}" -f $Target, $Configuration, $ProjectFile)
& $BuildBat $Target Win64 $Configuration "-project=$ProjectFile" -WaitMutex
$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Pending 'build failed'
    Write-Output ("BUILD-FAILED: Build.bat exited {0}; .octopus\build-pending stays." -f $code)
    exit 4
}
if (Test-Path -LiteralPath $Pending) { Remove-Item -LiteralPath $Pending -Force }
Write-Output 'BUILT'
exit 0
