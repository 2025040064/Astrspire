param([switch]$Apply, [switch]$InstalledReleaseVerified)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$projectPrefix = $projectRoot + '\'
$reportDir = Join-Path $projectRoot 'docs/maintenance'
$scanErrors = @()
$candidates = [Collections.Generic.List[string]]::new()
$versionSource = Get-Content -LiteralPath (Join-Path $projectRoot 'src/qx_stc_isp/__init__.py') -Raw
if ($versionSource -notmatch '__version__ = "0\.7\.0"') {
    throw 'This dated maintenance script is pinned to 0.7.0; review it before reusing'
}

if ($InstalledReleaseVerified) {
    $installedManifest = Join-Path $projectRoot 'Astrspire/versions/0.7.0/build-manifest.json'
    $stagedManifest = Join-Path $projectRoot 'build-release/0.7.0/app/Astrspire/build-manifest.json'
    if (-not (Test-Path -LiteralPath $installedManifest) -or -not (Test-Path -LiteralPath $stagedManifest) -or
        (Get-FileHash -LiteralPath $installedManifest -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $stagedManifest -Algorithm SHA256).Hash) {
        throw 'Installed release does not match the staged release; preserve current artifacts'
    }
}

function Add-Candidate([string]$relativePath) {
    $path = [IO.Path]::GetFullPath((Join-Path $projectRoot $relativePath))
    if (-not $path.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Target is outside project: $relativePath"
    }
    if (Test-Path -LiteralPath $path) { $candidates.Add($path) }
}

foreach ($name in @('.pytest_cache', '.pytest-tmp', '.uv-cache', 'build', 'build-release/0.7.0/work',
    'build-release/0.7.0/spec', 'build-release/tmp', 'build-release/pyi-cache',
    'build-preview/dev5/test-appdata', 'build-preview/dev5/test-localappdata',
    'build-preview/dev5/tmp', 'build-preview/publish-clean-source',
    'build-preview/publish-20261028/diagnostic',
    'build-preview/publish-20261028/failed-qt-0.7.0',
    'build-preview/publish-20261028/failed-install-0.7.0/_internal',
    'build-preview/publish-20261028/failed-install-0.7.0/Astrspire.exe',
    'build-preview/publish-20261028/failed-install-0.7.0/build-manifest.json')) { Add-Candidate $name }
if ($InstalledReleaseVerified) { Add-Candidate 'build-release/0.7.0' }

# dist contains generated releases only. Keep the current release variants.
Get-ChildItem -LiteralPath (Join-Path $projectRoot 'dist') -Force | ForEach-Object {
    if ($_.Name -match '^(Astrspire-v|QX-STC-ISP)' -and
        $_.Name -notmatch '^Astrspire-v0\.7\.0(?:$|[.\-])' -and
        $_.Name -notmatch 'private-hardware') { $candidates.Add($_.FullName) }
}

# Keep screenshots at the root of build-clean, remove generated bundler work.
Get-ChildItem -LiteralPath (Join-Path $projectRoot 'build-clean') -Force | ForEach-Object {
    if ($_.PSIsContainer -or $_.Extension -eq '.spec') { $candidates.Add($_.FullName) }
}

foreach ($name in @('src', 'tests', 'tools', 'build-preview')) {
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push((Join-Path $projectRoot $name))
    while ($pending.Count -gt 0) {
        $folder = $pending.Pop()
        try { $children = @(Get-ChildItem -LiteralPath $folder -Force -ErrorAction Stop) }
        catch { $scanErrors += @{path=$folder.Substring($projectPrefix.Length); reason='scan_access_failed'}; continue }
        foreach ($child in $children) {
            if (-not $child.PSIsContainer) { continue }
            if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
            if ($child.Name -eq '__pycache__' -or $child.Name -eq '.pytest_cache' -or
                $child.Name -match '^_MEI\d+$' -or $child.Name -match '^pytest-') {
                $candidates.Add($child.FullName)
            } elseif ($child.Name -ne '.git') { $pending.Push($child.FullName) }
        }
    }
}

$selected = [Collections.Generic.List[string]]::new()
foreach ($path in @($candidates | Sort-Object Length, @{Expression={$_}} -Unique)) {
    if (-not @($selected | Where-Object { $path.StartsWith($_ + '\', [StringComparison]::OrdinalIgnoreCase) }).Count) {
        $selected.Add($path)
    }
}
$rows = @()
foreach ($path in $selected) {
    $relative = $path.Substring($projectPrefix.Length)
    $row = [ordered]@{path=$relative; files=0; bytes=0; status='planned'}
    try {
        $resolved = (Resolve-Path -LiteralPath $path -ErrorAction Stop).Path
        if (-not $resolved.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Resolved target escaped project' }
        $ancestor = Split-Path -Parent $resolved
        while (-not $ancestor.Equals($projectRoot, [StringComparison]::OrdinalIgnoreCase)) {
            if ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Reparse ancestor rejected'
            }
            $ancestor = Split-Path -Parent $ancestor
        }
        $item = Get-Item -LiteralPath $path -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse target rejected' }
        if ($relative -match 'failed-qt' -and
            ((Test-Path -LiteralPath (Join-Path $path 'data')) -or (Test-Path -LiteralPath (Join-Path $path 'logs')))) {
            throw 'Runtime data found in failed bundle; target preserved'
        }
        $files = if ($item.PSIsContainer) { @(Get-ChildItem -LiteralPath $path -Recurse -Force -File -ErrorAction Stop) } else { @($item) }
        $links = if ($item.PSIsContainer) { @(Get-ChildItem -LiteralPath $path -Recurse -Force -Attributes ReparsePoint -ErrorAction Stop) } else { @() }
        if ($links.Count) { throw 'Nested reparse target rejected' }
        if (@($files | Where-Object { $_.Name -match '(?i)(original[-_]flash|backup|flash[-_]dump).*\.(bin|hex)$' }).Count) {
            throw 'Firmware backup found; target preserved'
        }
        $row.files = $files.Count
        $row.bytes = ($files | Measure-Object Length -Sum).Sum
        if ($null -eq $row.bytes) { $row.bytes = 0 }
        if ($Apply) {
            Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction Stop
            $row.status = 'deleted'
        }
    } catch { $row.status = 'preserved'; $row['reason'] = $_.Exception.Message }
    $rows += [pscustomobject]$row
}

New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
$report = [ordered]@{date='2026-10-28'; applied=[bool]$Apply; items=$rows; scan_errors=$scanErrors}
$reportPath = Join-Path $reportDir $(if ($Apply) {'cleanup-2026-10-28.json'} else {'cleanup-preview-2026-10-28.json'})
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding utf8
$rows | Group-Object status | ForEach-Object {
    [pscustomobject]@{Status=$_.Name; Targets=$_.Count; Files=($_.Group | Measure-Object files -Sum).Sum;
        MiB=[math]::Round((($_.Group | Measure-Object bytes -Sum).Sum / 1MB), 2)}
} | Format-Table -AutoSize
$rows | Where-Object status -eq 'preserved' | Format-Table path, reason -AutoSize
Write-Output "Report: $reportPath"
