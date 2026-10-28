param([Parameter(Mandatory=$true)][string]$Executable, [string]$ExpectedTitle)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
if (-not $ExpectedTitle) {
    $taskVersionLine = Select-String -LiteralPath (Join-Path $taskRoot 'src\qx_stc_isp\__init__.py') -Pattern '^__version__ = "([^"]+)"$'
    if ($null -eq $taskVersionLine) { throw 'Cannot read the application version.' }
    $ExpectedTitle = 'Astrspire v' + ($taskVersionLine.Matches[0].Groups[1].Value -replace '\.dev', '-dev')
}
$taskExe = (Resolve-Path -LiteralPath $Executable).Path
$taskDist = [IO.Path]::GetFullPath((Join-Path $taskRoot 'dist')) + '\'
if (-not $taskExe.StartsWith($taskDist, [StringComparison]::OrdinalIgnoreCase)) { throw 'Only task dist executables can be checked.' }
$taskOutput = Join-Path $taskRoot 'build-preview\dev4'
$taskEmpty = Join-Path $taskOutput 'empty-startup'
New-Item -ItemType Directory -Path $taskEmpty -Force | Out-Null
$taskName = [IO.Path]::GetFileNameWithoutExtension($taskExe)
$taskBefore = @(Get-Process -Name $taskName -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
# This is the interactive application under test, not a background helper.
# A visible main window is necessary evidence; it is closed after the check.
$taskStarted = Start-Process -FilePath $taskExe -WorkingDirectory $taskEmpty -WindowStyle Normal -PassThru
$taskDeadline = [DateTime]::UtcNow.AddSeconds(25)
$taskWindow = $null
try {
    do {
        Start-Sleep -Milliseconds 200
        $taskWindow = Get-Process -Name $taskName -ErrorAction SilentlyContinue | Where-Object {
            $_.Id -notin $taskBefore -and $_.Path -eq $taskExe -and $_.MainWindowTitle -eq $ExpectedTitle
        } | Select-Object -First 1
    } until ($null -ne $taskWindow -or [DateTime]::UtcNow -ge $taskDeadline)
    if ($null -eq $taskWindow) { throw 'No matching main window appeared within 25 seconds.' }
    $taskResult = [ordered]@{ path=$taskExe; sha256=(Get-FileHash -LiteralPath $taskExe -Algorithm SHA256).Hash
        title=$taskWindow.MainWindowTitle; responding=$taskWindow.Responding; process_id=$taskWindow.Id
        started_utc=[DateTime]::UtcNow.ToString('o'); working_directory=$taskEmpty
        scope='Startup only; no hardware connection or application TX requested.' }
    if (-not $taskWindow.Responding) { throw 'Main window is not responding.' }
    $taskResult | ConvertTo-Json | Out-File -LiteralPath (Join-Path $taskOutput ($taskName + '-' + (Split-Path -Leaf (Split-Path -Parent $taskExe)) + '-startup.json')) -Encoding utf8
    $taskResult | ConvertTo-Json
} finally {
    if ($null -ne $taskWindow) {
        $null = $taskWindow.CloseMainWindow()
        $taskWindow.WaitForExit(8000) | Out-Null
        if (-not $taskWindow.HasExited) { throw 'The checked window did not close normally.' }
    }
}
