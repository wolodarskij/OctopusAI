# Set up a Windows machine to run Octopus AI. Run tools\setup-windows.bat from the checkout:
#
#     tools\setup-windows.bat                           ask before each install
#     tools\setup-windows.bat -Yes                      install whatever is missing without asking
#     tools\setup-windows.bat -Providers claude,codex   these AI providers' command-line tools: claude, codex
#
# AgentBoard.bat runs it by itself, with -Yes, when Python or git is missing.
#
# What it makes sure of, installing only what is missing, for this user (an installer that needs admin rights
# asks for them itself):
#   1. winget                   the package manager the rest comes from when it is there; every step has a
#                               fallback without it
#   2. python on the PATH       Python 3.10 or newer, and the plain `python` command the hooks, the briefs and
#                               AgentBoard.bat run (not the Microsoft Store's alias)
#   3. git                      Git for Windows; Claude Code also runs its shell in Git Bash
#   4. Python packages          none to install: Octopus AI uses only Python's standard library; the step checks it,
#                               tkinter too (the board's folder dialog)
#   5. Windows Terminal         the `wt` an agent opens in; Windows 11 has it, Windows 10 gets it here
#   6. AI providers             Claude Code, Codex (with Node.js, only when npm is missing), as chosen;
#                               with -Yes and no -Providers, Claude Code
# Then it runs the checks: each tool's version, what failed and how to fix it, and `python octopus.py --help`.
# Last, it asks for the default AI provider and model and checks them (`python octopus.py setup`). With -Yes or
# -Board it does not ask: the board asks in its page.
#
# It changes nothing else, and it is safe to run again: a step that is done says so and moves on.

param(
    [switch]$Yes,
    [string[]]$Providers,
    # AgentBoard.bat runs the script with -Board when Python or git is missing; the board starts next
    [switch]$Board,
    # the python.org installer the fallback downloads when winget is missing
    [string]$PythonVersion = '3.13.5'
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'   # Windows PowerShell's progress bar slows every download down
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $PSScriptRoot
$Arm = ($env:PROCESSOR_ARCHITEW6432, $env:PROCESSOR_ARCHITECTURE | Where-Object { $_ } | Select-Object -First 1) -eq 'ARM64'
$Temp = Join-Path $env:TEMP 'octopus-setup'
$MinPython = [version]'3.10'
$Report = New-Object System.Collections.ArrayList
$Session = New-Object System.Collections.ArrayList   # folders put on this run's PATH

function Say($text) { Write-Host $text -ForegroundColor Cyan }
function Ok($text) { Write-Host "  ok  $text" }
function Bad($text) { Write-Host "  !!  $text" -ForegroundColor Yellow }
function Ask($question, [bool]$default = $true) {
    if ($Yes) { return $true }
    $hint = if ($default) { '[Y/n]' } else { '[y/N]' }
    $a = Read-Host "  $question $hint"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default }
    return $a.Trim() -match '^[yY]'
}
function Note($tool, [bool]$ok, $detail, $fix = '') {
    [void]$Report.Add([pscustomobject]@{ Tool = $tool; Ok = $ok; Detail = $detail; Fix = $fix })
}

# The first line a command prints, or $null when it fails or is missing.
function First-Line($exe, [string[]]$arguments = @()) {
    try { $out = @(& $exe @arguments 2>$null) } catch { return $null }
    if ($LASTEXITCODE -ne 0 -or $out.Count -eq 0) { return $null }
    return "$($out[0])".Trim()
}

# -- the PATH --------------------------------------------------------------------
# The user PATH is read and written raw, so its %VARIABLES% stay unexpanded.
function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($Session) + @($machine, $user) | Where-Object { $_ }) -join ';'
}
function Same-Dir($a, $b) {
    return ([Environment]::ExpandEnvironmentVariables($a)).TrimEnd('\') -ieq ([Environment]::ExpandEnvironmentVariables($b)).TrimEnd('\')
}
function Add-UserPath($dir, [switch]$First) {
    $key = Get-Item 'HKCU:\Environment'
    $raw = [string]$key.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
    $parts = @($raw -split ';' | Where-Object { $_ })
    foreach ($p in $parts) { if (Same-Dir $p $dir) { return $false } }
    $new = if ($First) { @($dir) + $parts } else { $parts + @($dir) }
    New-ItemProperty -Path 'HKCU:\Environment' -Name Path -Value ($new -join ';') -PropertyType ExpandString -Force | Out-Null
    # Setting a user variable through .NET tells Explorer, so a new terminal sees the new PATH.
    [Environment]::SetEnvironmentVariable('OCTOPUS_SETUP', '1', 'User')
    [Environment]::SetEnvironmentVariable('OCTOPUS_SETUP', $null, 'User')
    return $true
}
function Use-Dir($dir) {
    if (-not ($Session -contains $dir)) { [void]$Session.Insert(0, $dir) }
    Refresh-Path
}
# Puts the first of $dirs that holds $file on the user PATH unless the command already resolves.
function Ensure-OnPath($command, $file, [string[]]$dirs) {
    if (Get-Command $command -ErrorAction SilentlyContinue) { return $true }
    foreach ($d in $dirs) {
        if ($d -and (Test-Path (Join-Path $d $file))) {
            if (Add-UserPath $d) { Ok "added $d to your PATH" }
            Use-Dir $d
            return $true
        }
    }
    return $false
}

# -- installers ------------------------------------------------------------------
$Winget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
function Winget-Install($id, [string[]]$more = @()) {
    if (-not $Winget) { return $false }
    Write-Host "  winget install $id $more"
    & winget install -e --id $id --silent --accept-source-agreements --accept-package-agreements @more
    $code = $LASTEXITCODE
    Refresh-Path
    return ($code -eq 0)
}
function Download($url, $name) {
    New-Item -ItemType Directory -Force -Path $Temp | Out-Null
    $out = Join-Path $Temp $name
    Write-Host "  download $url"
    try { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $out; return $out }
    catch { Bad "the download failed: $($_.Exception.Message)"; return $null }
}
function Run-Installer($file, [string[]]$arguments) {
    if (-not $file) { return }
    Write-Host "  run $(Split-Path -Leaf $file)"
    Start-Process -FilePath $file -ArgumentList $arguments -Wait
    Refresh-Path
}
function Run-Script($line) {
    Write-Host "  $line"
    & powershell -NoProfile -ExecutionPolicy Bypass -Command $line
    Refresh-Path
}

if ([Environment]::OSVersion.Platform -ne 'Win32NT') {
    Write-Host 'This script sets up Windows. On a Mac run tools/setup-mac.sh.'
    exit 1
}

# -- 1. winget -------------------------------------------------------------------
Say '1. winget'
if ($Winget) {
    $v = First-Line 'winget' @('--version')
    Ok "winget $v"
    Note 'winget' $true $v
} else {
    Write-Host '  missing: every step below uses its fallback. It comes with "App Installer" from the Microsoft Store.'
    Note 'winget' $true 'missing; the fallbacks were used'
}

# -- 2. Python, and the `python` command -----------------------------------------
Say '2. Python'
function Python-Version($exe) {
    $v = First-Line $exe @('-c', 'import sys; print(''.''.join(map(str, sys.version_info[:3])))')
    if ($v -match '^\d+\.\d+\.\d+$') { return [version]$v }
    return $null
}
# The Python that `python` runs, or $null; the Microsoft Store's alias that only opens the Store is $null.
function Path-Python {
    $cmd = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) { return $null }
    $v = Python-Version 'python'
    if (-not $v) { return $null }
    return [pscustomobject]@{ Path = $cmd.Source; Version = $v }
}
# The newest installed python.exe of $MinPython or newer, wherever it is.
function Best-Python {
    $found = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $e = First-Line 'py' @('-3', '-c', 'import sys; print(sys.executable)')
        if ($e) { $found += $e }
    }
    foreach ($glob in @("$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe")) {
        $found += @(Get-ChildItem $glob -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    }
    $best = $null; $bestV = $null
    foreach ($exe in $found) {
        $v = Python-Version $exe
        if ($v -and $v -ge $MinPython -and (-not $bestV -or $v -gt $bestV)) { $best = $exe; $bestV = $v }
    }
    return $best
}

$py = Path-Python
if (-not $py -or $py.Version -lt $MinPython) {
    if ($py) { Bad "python on the PATH is $($py.Version) at $($py.Path); Octopus AI needs $MinPython or newer" }
    $exe = Best-Python
    if (-not $exe -and (Ask "Install Python 3 for this user?")) {
        if (-not (Winget-Install 'Python.Python.3.13' @('--scope', 'user'))) {
            $suffix = if ($Arm) { 'arm64' } else { 'amd64' }
            $file = Download "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-$suffix.exe" 'python-setup.exe'
            Run-Installer $file @('/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=1',
                                  'InstallLauncherAllUsers=0', 'Include_test=0')
        }
        $py = Path-Python
        if (-not $py -or $py.Version -lt $MinPython) { $exe = Best-Python }
    }
    if ($exe -and (-not $py -or $py.Version -lt $MinPython)) {
        $dir = Split-Path -Parent $exe
        if (Ask "Put $dir first on your PATH, so ``python`` runs it?") {
            Add-UserPath "$dir\Scripts" -First | Out-Null
            if (Add-UserPath $dir -First) { Ok "added $dir to your PATH" }
        }
        Use-Dir "$dir\Scripts"
        Use-Dir $dir
        $py = Path-Python
    }
}
if ($py -and $py.Version -ge $MinPython) {
    Ok "Python $($py.Version) at $($py.Path)"
    Note 'python' $true "$($py.Version) at $($py.Path)"
} elseif ($py) {
    Bad "python is still $($py.Version) at $($py.Path)"
    Note 'python' $false "$($py.Version) at $($py.Path)" "an older Python comes first on the system PATH: uninstall it, or remove its folder from the system PATH, then run this script again"
} else {
    Bad 'python is missing'
    Note 'python' $false 'missing' "install Python $MinPython or newer from https://www.python.org/downloads/ with ""Add python.exe to PATH"" ticked, then run this script again"
}

# -- 3. git ----------------------------------------------------------------------
Say '3. git'
$gitDirs = @("$env:LOCALAPPDATA\Programs\Git\cmd", "$env:ProgramFiles\Git\cmd")
if (-not (Ensure-OnPath 'git' 'git.exe' $gitDirs) -and (Ask 'Install Git for Windows?')) {
    if (-not (Winget-Install 'Git.Git' @('--scope', 'user')) -and -not (Winget-Install 'Git.Git')) {
        try {
            $release = Invoke-RestMethod -UseBasicParsing 'https://api.github.com/repos/git-for-windows/git/releases/latest'
            $pattern = if ($Arm) { '^Git-[\d.]+-arm64\.exe$' } else { '^Git-[\d.]+-64-bit\.exe$' }
            $asset = $release.assets | Where-Object { $_.name -match $pattern } | Select-Object -First 1
            if ($asset) {
                $file = Download $asset.browser_download_url 'git-setup.exe'
                Run-Installer $file @('/VERYSILENT', '/NORESTART', '/NOCANCEL', '/SP-', '/CURRENTUSER')
            } else { Bad 'no Git for Windows installer in the latest release' }
        } catch { Bad "the Git for Windows release could not be read: $($_.Exception.Message)" }
    }
    Ensure-OnPath 'git' 'git.exe' $gitDirs | Out-Null
}
$v = First-Line 'git' @('--version')
if ($v) { Ok $v; Note 'git' $true $v }
else { Bad 'git is missing'; Note 'git' $false 'missing' 'install Git for Windows from https://git-scm.com/download/win, then run this script again' }

# -- 4. Python packages ----------------------------------------------------------
Say '4. Python packages'
if ($py) {
    & python -c 'import ctypes, http.server, json, msvcrt, socket, subprocess, tkinter, urllib.request, webbrowser, _winapi' 2>$null
    if ($LASTEXITCODE -eq 0) {
        Ok 'none to install: Octopus AI uses only the standard library, and it is complete'
        Note 'packages' $true 'standard library only'
    } else {
        Bad 'this Python lacks part of its standard library'
        Note 'packages' $false 'standard library incomplete' 'reinstall Python from https://www.python.org/downloads/'
    }
} else {
    Bad 'skipped: python is missing'
}

# -- 5. Windows Terminal ---------------------------------------------------------
Say '5. Windows Terminal'
if (-not (Get-Command wt -ErrorAction SilentlyContinue)) {
    if (Ask 'Windows Terminal (wt) is missing, and agents open in it. Install it?') {
        Winget-Install 'Microsoft.WindowsTerminal' | Out-Null
    }
}
if (Get-Command wt -ErrorAction SilentlyContinue) {
    $v = $null
    try { $v = (Get-AppxPackage -Name 'Microsoft.WindowsTerminal*' -ErrorAction Stop | Select-Object -First 1).Version } catch { }
    $text = if ($v) { "Windows Terminal $v" } else { 'Windows Terminal' }
    Ok $text
    Note 'wt' $true $text
} else {
    Bad 'Windows Terminal is missing'
    Note 'wt' $false 'missing' 'install it from the Microsoft Store (https://aka.ms/terminal), or choose cmd in the board''s Settings, Terminals'
}

# -- 6. AI providers -------------------------------------------------------------
Say '6. AI providers'
$known = [ordered]@{
    claude = @{ Label = 'Claude Code'; Command = 'claude'; Login = 'run `claude` once and log in with your Claude account' }
    codex  = @{ Label = 'Codex';       Command = 'codex';  Login = 'run `codex login`' }
}
$chosen = $null
if ($Providers) {
    $chosen = @($Providers -split ',' | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ })
    foreach ($c in $chosen) { if (-not $known.Contains($c)) { Bad "unknown AI provider '$c': claude or codex" } }
}
function Wanted($id) {
    if ($chosen) { return $chosen -contains $id }
    if ($Yes) { return $id -eq 'claude' }
    return Ask "Install $($known[$id].Label)?" ($id -eq 'claude')
}

function Install-Node {
    $nodeDirs = @("$env:ProgramFiles\nodejs", "$env:LOCALAPPDATA\Programs\nodejs")
    if (Ensure-OnPath 'npm.cmd' 'npm.cmd' $nodeDirs) { return }
    Write-Host '  Codex installs with npm, which comes with Node.js.'
    if (-not (Winget-Install 'OpenJS.NodeJS.LTS')) {
        try {
            $lts = Invoke-RestMethod -UseBasicParsing 'https://nodejs.org/dist/index.json' | Where-Object { $_.lts } | Select-Object -First 1
            $name = "node-$($lts.version)-win-$(if ($Arm) { 'arm64' } else { 'x64' })"
            $zip = Download "https://nodejs.org/dist/$($lts.version)/$name.zip" 'node.zip'
            if ($zip) {
                Expand-Archive -Path $zip -DestinationPath $Temp -Force
                $dest = "$env:LOCALAPPDATA\Programs\nodejs"
                New-Item -ItemType Directory -Force -Path $dest | Out-Null
                Copy-Item -Path (Join-Path $Temp "$name\*") -Destination $dest -Recurse -Force
            }
        } catch { Bad "Node.js could not be downloaded: $($_.Exception.Message)" }
    }
    Ensure-OnPath 'npm.cmd' 'npm.cmd' $nodeDirs | Out-Null
}

$localBin = Join-Path $HOME '.local\bin'
Ensure-OnPath 'claude' 'claude.exe' @($localBin) | Out-Null
$codexBinRoot = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin'
$codexDesktopDirs = @(Get-ChildItem -LiteralPath $codexBinRoot -Directory -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | ForEach-Object { $_.FullName })
Ensure-OnPath 'codex' 'codex.exe' ($codexDesktopDirs + @($codexBinRoot)) | Out-Null
Ensure-OnPath 'codex' 'codex.cmd' @("$env:APPDATA\npm") | Out-Null
$installed = @()
foreach ($id in $known.Keys) {
    $p = $known[$id]
    if (-not (Get-Command $p.Command -ErrorAction SilentlyContinue)) {
        if (-not (Wanted $id)) { Write-Host "  $($p.Label): not installed"; continue }
        switch ($id) {
            'claude' {
                Run-Script 'irm https://claude.ai/install.ps1 | iex'
                Ensure-OnPath 'claude' 'claude.exe' @($localBin) | Out-Null
            }
            'codex' {
                Install-Node
                if (Get-Command npm.cmd -ErrorAction SilentlyContinue) {
                    Write-Host '  npm install -g @openai/codex'
                    & npm.cmd install -g '@openai/codex'
                    Ensure-OnPath 'codex' 'codex.cmd' @((First-Line 'npm.cmd' @('prefix', '-g')), "$env:APPDATA\npm") | Out-Null
                } else { Bad 'npm is missing, so Codex cannot be installed' }
            }
        }
    }
    $v = First-Line $p.Command @('--version')
    if (Get-Command $p.Command -ErrorAction SilentlyContinue) {
        $text = if ($v) { "$($p.Label) $v" } else { $p.Label }
        Ok "$text; to log in: $($p.Login)"
        Note $p.Command $true $text
        $installed += $id
    } else {
        Bad "$($p.Label) did not install"
        $fix = switch ($id) {
            'claude' { 'see https://docs.claude.com/en/docs/claude-code/setup' }
            'codex'  { 'install Node.js from https://nodejs.org, then run: npm install -g @openai/codex' }
        }
        Note $p.Command $false 'did not install' $fix
    }
}
if ($installed.Count -eq 0) {
    Note 'AI provider' $false 'none installed' 'an agent needs one: run this script again with -Providers claude'
}

# -- the checks ------------------------------------------------------------------
Say 'Checks'
if ($py) {
    Push-Location $Root
    & python octopus.py --help *> $null
    if ($LASTEXITCODE -eq 0) { Note 'octopus.py' $true 'python octopus.py runs' }
    else { Note 'octopus.py' $false 'python octopus.py --help failed' "run it in $Root to see the error" }
    Pop-Location
}
foreach ($r in $Report) {
    if ($r.Ok) { Write-Host ("  ok  {0,-12} {1}" -f $r.Tool, $r.Detail) }
    else {
        Write-Host ("  !!  {0,-12} {1}" -f $r.Tool, $r.Detail) -ForegroundColor Yellow
        Write-Host ("      {0,-12} fix: {1}" -f '', $r.Fix)
    }
}
Write-Host ''
if (@($Report | Where-Object { -not $_.Ok }).Count -gt 0) {
    Write-Host 'Fix each line marked !!, then run tools\setup-windows.bat again.'
    exit 1
}

# -- the default AI provider and model --------------------------------------------
if (-not $Yes -and -not $Board) {
    Say 'Default AI provider and model'
    Push-Location $Root
    & python octopus.py setup
    Pop-Location
}
if ($Board) {
    Write-Host 'Done. The Agent Board starts now. To log in to an AI provider, in a new terminal:'
    foreach ($id in $installed) { Write-Host "  $($known[$id].Login)" }
    exit 0
}
Write-Host 'Done. Open a new terminal (it reads the new PATH), then:'
foreach ($id in $installed) { Write-Host "  $($known[$id].Login)" }
Write-Host '  AgentBoard.bat --project <your project folder>'
Write-Host '      the board in your browser; a folder in no project, an empty one too, gets Octopus AI first'
Write-Host '  AgentBoard.bat'
Write-Host '      the board of every registered project; with none, it asks for the folder'
