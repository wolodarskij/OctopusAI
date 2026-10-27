<#
.SYNOPSIS
    Launches the Unreal editor for a project with the MCP server started and the
    Claude Code client config regenerated.

.DESCRIPTION
    Used when the editor is down and a scheduled/cron task cannot run because the
    unreal-mcp tools are unreachable.

    Refuses to launch if an editor is already running or the MCP port already answers,
    so it can be run safely without checking first.

    Launches detached: the editor outlives this script and this session.

    When <project>\.octopus\build-pending exists, it builds the editor target first with
    build_editor.ps1, and exits 5 BUILD-FAILED without a launch when the build fails.

    The project and the engine come from, in this order:

      project  -ProjectDir, then OCTOPUS_PROJECT, then the nearest directory at or above
               the current directory that holds a *.uproject file
      engine   -EngineDir, then "engineDir" in <project>/.octopus/unreal.json, then
               UE_ENGINE_DIR

    <project>/.octopus/unreal.json also supplies "uproject" (the .uproject file name, when
    the directory holds more than one), "editorLog" (default Saved/Logs/<uproject name>.log)
    and "mcpUrl" (default http://127.0.0.1:8000/mcp).

.PARAMETER ProjectDir
    The project directory, the one that holds the .uproject file.

.PARAMETER EngineDir
    The engine root, the directory that holds Engine\Binaries\Win64\UnrealEditor.exe.

.PARAMETER Wait
    Poll the MCP endpoint until it answers, then exit. Without this the script returns
    as soon as the process is spawned.

.PARAMETER TimeoutSeconds
    How long -Wait polls before giving up. Default 300 (a cold editor start is ~1-3 min).

.PARAMETER Force
    Launch even if an editor process is already running. Almost never what you want.

.PARAMETER KillExisting
    Only acts when the running editor has NEVER ticked a frame - i.e. it is blocked on a
    startup modal, which is the autosave restore prompt in practice. Kills it and relaunches
    with -AutoDeclinePackageRecovery so the prompt cannot reappear.

    Safe by construction: a process that has not ticked a frame never opened the editor UI,
    so no asset can have been opened or dirtied and there is nothing unsaved to lose. The
    script refuses to kill an editor that HAS ticked frames, because that one may hold work.

.EXAMPLE
    powershell -File start_editor.ps1 -ProjectDir C:\Work\MyGame -Wait

.EXAMPLE
    # editor is wedged on the autosave restore dialog
    powershell -File start_editor.ps1 -ProjectDir C:\Work\MyGame -KillExisting -Wait
#>
[CmdletBinding()]
param(
    [string] $ProjectDir,
    [string] $EngineDir,
    [switch] $Wait,
    [int]    $TimeoutSeconds = 300,
    [switch] $Force,
    [switch] $KillExisting
)

$ErrorActionPreference = 'Stop'

# --- configuration -------------------------------------------------------------------
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

# The engine path is explicit: a .uproject whose EngineAssociation is a GUID (a source build or
# a fork) matches no registered engine, so the launcher cannot resolve it.
if (-not $EngineDir) { $EngineDir = Get-Setting 'engineDir' $env:UE_ENGINE_DIR }
if (-not $EngineDir) {
    throw 'No engine: pass -EngineDir <path>, set "engineDir" in .octopus\unreal.json, or set UE_ENGINE_DIR.'
}
$EditorExe = Join-Path $EngineDir 'Engine\Binaries\Win64\UnrealEditor.exe'

$UprojectName = Get-Setting 'uproject' ''
if ($UprojectName) {
    $ProjectFile = Join-Path $ProjectRoot $UprojectName
}
else {
    $first = Get-ChildItem -LiteralPath $ProjectRoot -Filter *.uproject -File | Sort-Object Name | Select-Object -First 1
    if (-not $first) { throw "No .uproject file in $ProjectRoot" }
    $ProjectFile = $first.FullName
}

$McpUrl     = Get-Setting 'mcpUrl' 'http://127.0.0.1:8000/mcp'   # UModelContextProtocolSettings::ServerPortNumber default
$McpPort    = ([Uri] $McpUrl).Port
$ConfigFile = Join-Path $ProjectRoot '.mcp.json'
$LogRel     = Get-Setting 'editorLog' ('Saved\Logs\' + [IO.Path]::GetFileNameWithoutExtension($ProjectFile) + '.log')
$LogFile    = if ([IO.Path]::IsPathRooted($LogRel)) { $LogRel } else { Join-Path $ProjectRoot $LogRel }

function Test-McpAlive {
    # The endpoint is a streamable-HTTP MCP server: a bare GET returns an error status
    # rather than 200, so ANY HTTP response means the server is up. Only a connection
    # failure means it is down.
    try {
        Invoke-WebRequest -Uri $McpUrl -Method Get -TimeoutSec 3 -UseBasicParsing | Out-Null
        return $true
    }
    catch [System.Net.WebException] {
        if ($null -ne $_.Exception.Response) { return $true }
        return $false
    }
    catch {
        if ($_.Exception.Message -match 'status code|400|404|405|406') { return $true }
        return $false
    }
}

# --- guard: already running? ---------------------------------------------------------
if (-not $Force) {
    if (Test-McpAlive) {
        Write-Output ("ALREADY-UP: MCP server is answering on port {0}. Nothing to do." -f $McpPort)
        exit 0
    }

    $running = Get-Process -Name 'UnrealEditor' -ErrorAction SilentlyContinue
    if ($running) {
        Write-Output ("EDITOR-RUNNING-NO-MCP: UnrealEditor is running (PID {0}) but the MCP server is not answering." -f ($running.Id -join ', '))

        # Distinguish "server never started" from "started but the game thread is blocked".
        # A startup modal (usually the autosave restore prompt) leaves the listener created
        # but never accept()ed: the socket reports Listen and every connect is refused, and
        # the log's frame counter never leaves 0 because the first tick never ran.
        if (Test-Path -LiteralPath $LogFile) {
            $ticked = Select-String -LiteralPath $LogFile -Pattern '^\[[^\]]*\]\[\s*[1-9][0-9]*\]' -List -ErrorAction SilentlyContinue
            if (-not $ticked) {
                Write-Output 'DIAGNOSIS: no log line has a non-zero frame counter - the editor has NEVER ticked a frame.'
                Write-Output '           That is a startup modal blocking the game thread, almost always the autosave'
                Write-Output '           restore prompt. Nothing is unsaved (no frame ran), so the process is safe to kill.'
                Write-Output '           Fix: re-run this script with -KillExisting; it relaunches with -AutoDeclinePackageRecovery.'
                if ($KillExisting) {
                    foreach ($p in $running) {
                        Write-Output ("KILLING: PID {0}" -f $p.Id)
                        Stop-Process -Id $p.Id -Force
                    }
                    Start-Sleep -Seconds 3
                }
                else { exit 2 }
            }
            else {
                Write-Output 'The editor has ticked frames, so this is not the startup-modal case.'
                Write-Output 'Run ModelContextProtocol.StartServer in its console, or relaunch with -Force after closing it.'
                exit 2
            }
        }
        else {
            Write-Output 'Run ModelContextProtocol.StartServer in its console, or relaunch with -Force after closing it.'
            exit 2
        }
    }
}

# --- preflight -----------------------------------------------------------------------
if (-not (Test-Path -LiteralPath $EditorExe))   { throw "Editor not found: $EditorExe" }
if (-not (Test-Path -LiteralPath $ProjectFile)) { throw "Project not found: $ProjectFile" }

# --- pending build -------------------------------------------------------------------
# Install leaves .octopus\build-pending when a pack replaced editor sources while the editor ran
# (the AssetTextExport plugin of the blueprint pack). The editor is down here, so build first.
if (Test-Path -LiteralPath (Join-Path $ProjectRoot '.octopus\build-pending')) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'build_editor.ps1') -ProjectDir $ProjectRoot -EngineDir $EngineDir -IfPending
    if ($LASTEXITCODE -eq 4) {
        Write-Output 'BUILD-FAILED: the pending build failed; the editor is not launched. Read the build output above.'
        exit 5
    }
}

# --- launch --------------------------------------------------------------------------
# -ModelContextProtocolStartServer  : UE::ModelContextProtocol::ShouldAutoStartServer()
#                                     starts the HTTP server at the correct point in
#                                     engine init. Better than an -ExecCmds StartServer,
#                                     which can fire before the module is ready.
# -ExecCmds=...GenerateClientConfig : writes .mcp.json next to the .uproject.
# -AutoDeclinePackageRecovery       : REQUIRED for unattended launches. If autosaves are
#                                     pending, FPackageAutoSaver::OfferToRestorePackages
#                                     shows a MODAL "restore files?" dialog during startup
#                                     and the game thread blocks on it forever - engine
#                                     init completes, the first tick never runs, and the
#                                     MCP listener is created but never accept()s, so the
#                                     port shows Listen while refusing every connection.
#                                     The engine's own comment says so:
#                                       Editor/UnrealEd/Private/PackageAutoSaver.cpp
#                                       "Don't offer to restore packages during automation
#                                        testing or when unattended; the dlg is modal and blocks"
#                                     The switch is read in the same file:
#                                       bAutoDeclineRecovery(FParse::Param(FCommandLine::Get(),
#                                                            TEXT("AutoDeclinePackageRecovery")))
#                                     and makes startup behave as if the user chose Decline,
#                                     then deletes the restore file so it does not re-prompt.
#                                     Preferred over -unattended, which suppresses this dialog
#                                     as a side effect of globally auto-answering every modal.
# -ModelContextProtocolPort=N       : UE::ModelContextProtocol::GetServerPortNumber() reads it
#                                     before ServerPortNumber of the project's settings, and
#                                     GenerateClientConfig writes the same port into .mcp.json.
#                                     Two projects on one machine need two ports.
$mcpArgs = @(
    "`"$ProjectFile`""
    '-ModelContextProtocolStartServer'
    "-ModelContextProtocolPort=$McpPort"
    '-AutoDeclinePackageRecovery'
    '-ExecCmds="ModelContextProtocol.GenerateClientConfig ClaudeCode"'
)

Write-Output "LAUNCHING: $EditorExe"
Write-Output ("ARGS: {0}" -f ($mcpArgs -join ' '))

$proc = Start-Process -FilePath $EditorExe -ArgumentList $mcpArgs -PassThru
Write-Output ("SPAWNED: PID {0}" -f $proc.Id)

if (-not $Wait) {
    Write-Output 'NOT-WAITING: pass -Wait to block until the MCP endpoint answers.'
    exit 0
}

# --- wait for readiness --------------------------------------------------------------
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$tick = 0
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    $tick += 5

    if ($proc.HasExited) {
        Write-Output ("FAILED: editor exited during startup with code {0}." -f $proc.ExitCode)
        Write-Output ("Check {0} for the reason." -f $LogFile)
        exit 3
    }

    if (Test-McpAlive) {
        Write-Output ("READY: MCP answering after ~{0}s." -f $tick)
        if (Test-Path -LiteralPath $ConfigFile) {
            Write-Output ("CONFIG: {0}" -f $ConfigFile)
            Get-Content -LiteralPath $ConfigFile -Raw | Write-Output
        }
        else {
            Write-Output "CONFIG-MISSING: $ConfigFile was not written - run ModelContextProtocol.GenerateClientConfig ClaudeCode in the editor console."
        }
        exit 0
    }
}

Write-Output ("TIMEOUT: MCP did not answer within {0}s. The editor may still be loading (PID {1})." -f $TimeoutSeconds, $proc.Id)
exit 4
