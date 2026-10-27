<#
.SYNOPSIS
    Builds this plugin against one installed engine with RunUAT BuildPlugin.

.DESCRIPTION
    Runs `RunUAT.bat BuildPlugin` for Win64 and writes the packaged plugin to -Out. Exit code is
    RunUAT's.

    For a 4.x engine it swaps the machine-global BuildConfiguration.xml for the duration of the
    build: a UE5 install writes `Compiler=VisualStudio2026` there, and a 4.x UnrealBuildTool dies
    on that value before it reads anything else. The original is restored in a `finally` block.

.EXAMPLE
    powershell -File Build\build-plugin.ps1 -Engine "C:\Program Files\Epic Games\UE_5.8" -Out D:\out\5.8
.EXAMPLE
    powershell -File Build\build-plugin.ps1 -Engine "C:\Program Files\Epic Games\UE_4.25" -Out D:\out\4.25 -Compiler VisualStudio2019
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $Engine,
    [Parameter(Mandatory = $true)] [string] $Out,
    [string] $Compiler = '',
    [string] $Platforms = 'Win64'
)

$ErrorActionPreference = 'Stop'

$plugin = Join-Path (Split-Path $PSScriptRoot -Parent) 'AssetTextExport.uplugin'
$runUat = Join-Path $Engine 'Engine\Build\BatchFiles\RunUAT.bat'
if (-not (Test-Path $plugin)) { throw "no plugin at $plugin" }
if (-not (Test-Path $runUat)) { throw "no RunUAT at $runUat" }

$isUE4 = (Get-Content (Join-Path $Engine 'Engine\Build\Build.version') | ConvertFrom-Json).MajorVersion -lt 5
$cfg = Join-Path $env:APPDATA 'Unreal Engine\UnrealBuildTool\BuildConfiguration.xml'
$bak = Join-Path $env:TEMP ('BuildConfiguration.user.{0}.xml' -f $PID)
$swapped = $false

try {
    if ($isUE4) {
        $running = Get-Process -Name UnrealBuildTool, MSBuild -ErrorAction SilentlyContinue
        if ($running) { throw 'a build is already running; the config swap would corrupt it' }
        if (Test-Path $cfg) { Copy-Item $cfg $bak -Force }
        New-Item -ItemType Directory -Force -Path (Split-Path $cfg) | Out-Null
        if (-not $Compiler) { $Compiler = 'VisualStudio2019' }
        @"
<?xml version="1.0" encoding="utf-8" ?>
<Configuration xmlns="https://www.unrealengine.com/BuildConfiguration">
    <WindowsPlatform>
        <Compiler>$Compiler</Compiler>
    </WindowsPlatform>
</Configuration>
"@ | Out-File -FilePath $cfg -Encoding utf8
        $swapped = $true
    }

    if ($isUE4) {
        # 4.x's RunUAT.bat resolves AutomationTool.exe through the shell PATH from PowerShell and
        # fails; the executable itself, run from Engine\Source, is what the bat would have started.
        $uat = Join-Path $Engine 'Engine\Binaries\DotNET\AutomationTool.exe'
        Push-Location (Join-Path $Engine 'Engine\Source')
        try { & $uat BuildPlugin -Plugin="$plugin" -Package="$Out" "-TargetPlatforms=$Platforms" -Rocket; $code = $LASTEXITCODE }
        finally { Pop-Location }
    }
    else {
        & $runUat BuildPlugin -Plugin="$plugin" -Package="$Out" "-TargetPlatforms=$Platforms" -Rocket
        $code = $LASTEXITCODE
    }
}
finally {
    if ($swapped) {
        if (Test-Path $bak) { Copy-Item $bak $cfg -Force; Remove-Item $bak -Force -ErrorAction SilentlyContinue }
        else { Remove-Item $cfg -Force -ErrorAction SilentlyContinue }
    }
}

exit $code
