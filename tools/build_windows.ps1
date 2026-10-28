param([switch]$OneFile, [ValidateSet('', '-uart-test', '-serial-assistant', '-serial-assistant-r2')][string]$NameSuffix = '')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Create the project virtual environment first.' }
$taskVersionLine = Select-String -LiteralPath (Join-Path $taskRoot 'src\qx_stc_isp\__init__.py') -Pattern '^__version__ = "([^"]+)"$'
if ($null -eq $taskVersionLine) { throw 'Cannot read the application version.' }
$taskOutput = ('Astrspire-v' + $taskVersionLine.Matches[0].Groups[1].Value) + $NameSuffix
$taskMode = '--onedir'
if ($OneFile) { $taskMode = '--onefile' }
$taskOldPath = $env:PATH
$taskOldTemp = $env:TEMP
$taskOldTmp = $env:TMP
$taskOldConfig = $env:PYINSTALLER_CONFIG_DIR
$taskOldBytecode = $env:PYTHONDONTWRITEBYTECODE
try {
    $taskTemp = [IO.Path]::GetFullPath((Join-Path $taskRoot 'build-clean\tmp-v060-dev4'))
    $taskConfig = [IO.Path]::GetFullPath((Join-Path $taskRoot 'build-clean\pyinstaller-cache'))
    $taskBundleTemp = [IO.Path]::GetFullPath((Join-Path $taskRoot 'build-preview\dev4\bundle-run'))
    foreach ($taskDirectory in @($taskTemp, $taskConfig, $taskBundleTemp)) {
        if ([IO.Path]::GetPathRoot($taskDirectory) -ne 'F:\') { throw 'Build and runtime paths must stay on drive F.' }
        New-Item -ItemType Directory -Path $taskDirectory -Force | Out-Null
    }
    $env:TEMP = $taskTemp
    $env:TMP = $taskTemp
    $env:PYINSTALLER_CONFIG_DIR = $taskConfig
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $taskRuntime = (& $taskPython -c 'import sys; print(sys.base_prefix)').Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Python runtime discovery failed.' }
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem;$taskRoot\.venv\Scripts;$taskRuntime"
    Push-Location -LiteralPath $taskRoot
    try {
        $taskRuntimeArguments = @()
        if ($OneFile) { $taskRuntimeArguments = @('--runtime-tmpdir', $taskBundleTemp) }
        $taskRuntimeArguments += @('--add-data', "$taskRoot\third_party\SerialWeb;third_party\SerialWeb")
        $taskRuntimeArguments += @('--add-data', "$taskRoot\third_party\Lissio;third_party\Lissio")
        $taskRuntimeArguments += @('--add-data', "$taskRoot\docs\SERIAL_TOOLS_DEV4.md;docs", '--add-data', "$taskRoot\docs\feature-status-0.6.0-dev4.md;docs")
        & $taskPython -m PyInstaller $taskMode @taskRuntimeArguments --windowed --noconfirm --name $taskOutput --paths src --copy-metadata pyqtgraph --copy-metadata numpy --add-data "$taskRoot\examples\led.hex;examples" --add-data "$taskRoot\examples\flashled.hex;examples" --add-data "$taskRoot\examples\telemetry_matrix.csv;examples" --add-data "$taskRoot\examples\engine_temperature.json;examples" --add-data "$taskRoot\docs\VOFA_WORKBENCH_GUIDE.md;docs" --add-data "$taskRoot\docs\feature-status-0.6.0-dev2.md;docs" --add-data "$taskRoot\assets\fonts;assets\fonts" --distpath dist --workpath build-clean --specpath build-clean app_launcher.py
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    } finally { Pop-Location }
} finally {
    $env:PATH = $taskOldPath
    foreach ($taskPair in @(@('TEMP', $taskOldTemp), @('TMP', $taskOldTmp), @('PYINSTALLER_CONFIG_DIR', $taskOldConfig), @('PYTHONDONTWRITEBYTECODE', $taskOldBytecode))) {
        if ($null -eq $taskPair[1]) { Remove-Item -LiteralPath ('Env:' + $taskPair[0]) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ('Env:' + $taskPair[0]) -Value $taskPair[1] }
    }
}
