<#
.SYNOPSIS
    Analyzes an ignore file for Content/ and optionally packs or unpacks non-versioned trees.

.DESCRIPTION
    - Default (no mode): lists which Content/ subdirectories are excluded vs tracked by your VCS ignore rules.
    - pack:  creates NotVersioned.zip in the project root containing all excluded Content/
             subtrees. Archive paths are relative to Content/ so extraction restores layout.
    - unpack: finds NotVersioned.zip in the project root and extracts into Content/.

.PARAMETER Command
    First positional argument: analyze | pack | unpack (optional; default analyze).

.PARAMETER Pack
    Create NotVersioned.zip (same as Command pack). Use this if positional args do not bind.

.PARAMETER Unpack
    Extract NotVersioned.zip into Content/ (same as Command unpack).

.PARAMETER Root
    Project root (directory containing Content/ and an ignore file). Alias: -ProjectDir.
    Default: OCTOPUS_PROJECT, then the nearest directory at or above the current directory
    that holds a *.uproject file.

.PARAMETER IgnoreFile
    Explicit ignore file to use (name or path). Overrides auto-detection.

    Auto-detection tries the names in "ignoreFiles" of <project>/.octopus/unreal.json, in
    order. The default list is .p4ignore, .gitignore.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $false)]
    [string]$Command,

    [switch]$Pack,
    [switch]$Unpack,

    [Alias('ProjectDir')]
    [string]$Root,

    [string]$IgnoreFile
)

# Project root: explicit -Root, else OCTOPUS_PROJECT, else the nearest directory with a .uproject.
if ([string]::IsNullOrWhiteSpace($Root)) { $Root = $env:OCTOPUS_PROJECT }
if ([string]::IsNullOrWhiteSpace($Root)) {
    $d = Get-Item -LiteralPath (Get-Location).Path
    while ($null -ne $d) {
        if (Get-ChildItem -LiteralPath $d.FullName -Filter *.uproject -File -ErrorAction SilentlyContinue) {
            $Root = $d.FullName
            break
        }
        $d = $d.Parent
    }
}
if ([string]::IsNullOrWhiteSpace($Root)) {
    Write-Error "No project: pass -Root <path>, set OCTOPUS_PROJECT, or run inside a directory with a *.uproject file."
    exit 1
}

# Resolve operation: switches are explicit; otherwise first positional Command; else analyze.
if ($Pack) {
    $Mode = 'pack'
} elseif ($Unpack) {
    $Mode = 'unpack'
} elseif (-not [string]::IsNullOrWhiteSpace($Command)) {
    $Mode = $Command.Trim().ToLowerInvariant()
} else {
    $Mode = 'analyze'
}

$validModes = @('analyze', 'pack', 'unpack')
if ($validModes -notcontains $Mode) {
    Write-Error "Unknown command '$Command'. Use: analyze, pack, unpack, or -Pack / -Unpack."
    exit 1
}

$supportedIgnoreNames = @(".p4ignore", ".gitignore")
$settingsFile = Join-Path $Root ".octopus\unreal.json"
if (Test-Path -LiteralPath $settingsFile) {
    $settings = Get-Content -LiteralPath $settingsFile -Raw | ConvertFrom-Json
    if ($null -ne $settings.ignoreFiles -and @($settings.ignoreFiles).Count -gt 0) {
        $supportedIgnoreNames = @($settings.ignoreFiles)
    }
}

function Resolve-IgnoreFilePath {
    param(
        [string]$Root,
        [string]$IgnoreFileOverride
    )

    if (-not [string]::IsNullOrWhiteSpace($IgnoreFileOverride)) {
        if ([System.IO.Path]::IsPathRooted($IgnoreFileOverride)) {
            return $IgnoreFileOverride
        }
        return (Join-Path $Root $IgnoreFileOverride)
    }

    foreach ($name in $supportedIgnoreNames) {
        $p = Join-Path $Root $name
        if (Test-Path -LiteralPath $p) { return $p }
    }

    return $null
}

$ignorePath = Resolve-IgnoreFilePath -Root $Root -IgnoreFileOverride $IgnoreFile
$contentPath  = Join-Path $Root "Content"
$zipPath      = Join-Path $Root "NotVersioned.zip"

if ([string]::IsNullOrWhiteSpace($ignorePath) -or -not (Test-Path -LiteralPath $ignorePath)) {
    $msg = "No ignore file found. "
    if (-not [string]::IsNullOrWhiteSpace($IgnoreFile)) {
        $msg += "Not found: $ignorePath"
    } else {
        $msg += "Looked for: " + ($supportedIgnoreNames -join ", ")
    }
    Write-Error $msg
    exit 1
}
if (-not (Test-Path $contentPath)) {
    Write-Error "Content/ directory not found at: $contentPath"; exit 1
}

# ---------------------------------------------------------------------------
# Parse rules from the ignore file
# ---------------------------------------------------------------------------
$rules = [System.Collections.Generic.List[PSCustomObject]]::new()

foreach ($raw in Get-Content -LiteralPath $ignorePath) {
    $line = $raw.Trim()
    if ($line -eq '' -or $line.StartsWith('#')) { continue }

    $negated = $line.StartsWith('!')
    $pattern = if ($negated) { $line.Substring(1).TrimStart('/') } else { $line.TrimStart('/') }

    $rules.Add([PSCustomObject]@{ Negated = $negated; Pattern = $pattern; Raw = $raw })
}

# ---------------------------------------------------------------------------
# Convert a .gitignore-style glob pattern to a regex
# ---------------------------------------------------------------------------
function ConvertTo-IgnoreRegex {
    param([string]$Pattern)

    $p = $Pattern.TrimEnd('/')

    $r = ''
    $i = 0
    while ($i -lt $p.Length) {
        if ($p[$i] -eq '*' -and ($i + 1) -lt $p.Length -and $p[$i+1] -eq '*') {
            if ($i + 2 -lt $p.Length -and $p[$i+2] -eq '/') {
                $r += '(.+/)?'
                $i += 3
            } else {
                $r += '.*'
                $i += 2
            }
        } elseif ($p[$i] -eq '*') {
            $r += '[^/]*'
            $i++
        } elseif ($p[$i] -eq '?') {
            $r += '[^/]'
            $i++
        } else {
            $r += [regex]::Escape($p[$i])
            $i++
        }
    }

    return "^$r(/.*)?$"
}

function Test-PatternMatch {
    param([string]$Pattern, [string]$RelPath)

    $RelPath = $RelPath.Replace('\', '/')
    $rx = ConvertTo-IgnoreRegex -Pattern $Pattern

    if ($RelPath -match $rx) { return $true }

    $rxAnywhere = $rx -replace '^\^', ''
    if ($RelPath -match $rxAnywhere) { return $true }

    return $false
}

function Get-IgnoreStatus {
    param([string]$RelPath)

    $ignored = $false

    foreach ($rule in $rules) {
        if (Test-PatternMatch -Pattern $rule.Pattern -RelPath $RelPath) {
            $ignored = -not $rule.Negated
        }
    }

    return $ignored
}

# ---------------------------------------------------------------------------
# Collect excluded top-level Content subdirectories (same as analyze)
# ---------------------------------------------------------------------------
$subdirs = Get-ChildItem -Path $contentPath -Directory -ErrorAction Stop

$excluded = [System.Collections.Generic.List[string]]::new()
$tracked  = [System.Collections.Generic.List[string]]::new()

foreach ($dir in $subdirs) {
    $rel = "Content/$($dir.Name)"
    if (Get-IgnoreStatus -RelPath $rel) {
        $excluded.Add($rel)
    } else {
        $tracked.Add($rel)
    }
}

# ---------------------------------------------------------------------------
# pack: zip excluded dirs; entry names = path relative to Content/
# ---------------------------------------------------------------------------
function Invoke-Pack {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    if ($excluded.Count -eq 0) {
        Write-Warning "No excluded Content/ subdirectories to pack."
        return
    }

    if (Test-Path $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }

    $zipStream = [System.IO.File]::Open($zipPath, [System.IO.FileMode]::CreateNew)
    try {
        $zip = [System.IO.Compression.ZipArchive]::new($zipStream, [System.IO.Compression.ZipArchiveMode]::Create)
        try {
            foreach ($rel in $excluded) {
                # rel is "Content/DirName"
                $dirName = $rel.Substring('Content/'.Length)
                $fullDir = Join-Path $contentPath $dirName
                if (-not (Test-Path -LiteralPath $fullDir)) { continue }

                $files = Get-ChildItem -LiteralPath $fullDir -Recurse -File -Force -ErrorAction SilentlyContinue
                foreach ($f in $files) {
                    $entryName = ($f.FullName.Substring($contentPath.Length).TrimStart('\', '/') -replace '\\', '/')
                    $entry = $zip.CreateEntry($entryName, [System.IO.Compression.CompressionLevel]::Optimal)
                    $entryStream = $entry.Open()
                    try {
                        $fileStream = [System.IO.File]::OpenRead($f.FullName)
                        try {
                            $fileStream.CopyTo($entryStream)
                        } finally { $fileStream.Dispose() }
                    } finally { $entryStream.Dispose() }
                }
            }
        } finally { $zip.Dispose() }
    } finally { $zipStream.Dispose() }

    Write-Host "Created $zipPath ($($excluded.Count) top-level folder(s))." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# unpack: extract zip into Content/ (preserves paths under Content/)
# ---------------------------------------------------------------------------
function Invoke-Unpack {
    if (-not (Test-Path -LiteralPath $zipPath)) {
        Write-Error "Not found: $zipPath"; exit 1
    }

    $contentFull = [System.IO.Path]::GetFullPath($contentPath)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName.EndsWith('/')) { continue }

            $destRel = $entry.FullName -replace '\\', '/'
            $destFull = [System.IO.Path]::GetFullPath((Join-Path $contentPath $destRel))

            if (-not $destFull.StartsWith($contentFull, [System.StringComparison]::OrdinalIgnoreCase)) {
                Write-Error "Zip entry escapes Content/: $($entry.FullName)"
                exit 1
            }

            $destDir = Split-Path -Parent $destFull
            if (-not (Test-Path -LiteralPath $destDir)) {
                New-Item -ItemType Directory -Path $destDir -Force | Out-Null
            }
            # Overwrite existing files
            if (Test-Path -LiteralPath $destFull) {
                Remove-Item -LiteralPath $destFull -Force
            }
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destFull, $false)
        }
    } finally {
        $zip.Dispose()
    }

    Write-Host "Extracted to $contentPath" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
switch ($Mode) {
    'pack'   { Invoke-Pack; exit 0 }
    'unpack' { Invoke-Unpack; exit 0 }
}

# ---------------------------------------------------------------------------
# analyze (default) — same output as before
# ---------------------------------------------------------------------------
$sep = '-' * 50

Write-Host ""
Write-Host $sep
Write-Host "  $(Split-Path -Leaf $ignorePath)  ->  Content/ subdirectory status"
Write-Host $sep

Write-Host ""
Write-Host "EXCLUDED  ($($excluded.Count))" -ForegroundColor Red
if ($excluded.Count -eq 0) {
    Write-Host "  (none found)" -ForegroundColor DarkGray
} else {
    foreach ($p in $excluded) { Write-Host "  [-]  $p" -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "TRACKED   ($($tracked.Count))" -ForegroundColor Green
if ($tracked.Count -eq 0) {
    Write-Host "  (none found)" -ForegroundColor DarkGray
} else {
    foreach ($p in $tracked) { Write-Host "  [+]  $p" -ForegroundColor Cyan }
}

Write-Host ""
Write-Host $sep
Write-Host ""
Write-Host "Pack excluded trees:   .\check-content-ignores.ps1 -Pack"
Write-Host "                       .\check-content-ignores.ps1 pack"
Write-Host "Unpack into Content/:  .\check-content-ignores.ps1 -Unpack"
Write-Host "                       .\check-content-ignores.ps1 unpack"
Write-Host ""
Write-Host "Override ignore file:  .\check-content-ignores.ps1 analyze -IgnoreFile .gitignore"
Write-Host ""
