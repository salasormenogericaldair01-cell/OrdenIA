param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

function Remove-BuildDirectory([string]$name) {
    $target = [IO.Path]::GetFullPath((Join-Path $projectRoot $name))
    if (-not $target.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Ruta de limpieza fuera del proyecto: $target"
    }
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

Remove-BuildDirectory "build"
Remove-BuildDirectory "dist"
New-Item -ItemType Directory -Force (Join-Path $projectRoot "build") | Out-Null

& $python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller no está instalado. Ejecuta: python -m pip install -e ".[packaging]"'
}

$version = (& $python -c "from ordenia import __version__; print(__version__)" ).Trim()
$parts = @($version.Split('.') | ForEach-Object { [int]$_ })
while ($parts.Count -lt 4) { $parts += 0 }
$versionFile = Join-Path $projectRoot "build\ordenia_version_info.txt"
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($($parts[0]), $($parts[1]), $($parts[2]), $($parts[3])),
    prodvers=($($parts[0]), $($parts[1]), $($parts[2]), $($parts[3])), mask=0x3f, flags=0x0,
    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'OrdenIA Project'),
    StringStruct('FileDescription', 'OrdenIA - Asistente local para organizar archivos'),
    StringStruct('FileVersion', '$version'),
    StringStruct('InternalName', 'OrdenIA'),
    StringStruct('OriginalFilename', 'OrdenIA.exe'),
    StringStruct('ProductName', 'OrdenIA'),
    StringStruct('ProductVersion', '$version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@ | Set-Content -LiteralPath $versionFile -Encoding ascii

$env:ORDENIA_VERSION_FILE = $versionFile
& $python -m PyInstaller --noconfirm --clean (Join-Path $projectRoot "packaging\ordenia.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló." }

$exe = Join-Path $projectRoot "dist\OrdenIA\OrdenIA.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "No se generó $exe" }
$smokeRoot = Join-Path $projectRoot "build\smoke-localappdata"
$report = Join-Path $projectRoot "build\smoke-report.json"
$previousLocalAppData = $env:LOCALAPPDATA
$previousQpa = $env:QT_QPA_PLATFORM
try {
    $env:LOCALAPPDATA = $smokeRoot
    $env:QT_QPA_PLATFORM = "offscreen"
    $process = Start-Process -FilePath $exe -ArgumentList @("--smoke-test", "--smoke-report", "`"$report`"") -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) { throw "El smoke test del ejecutable terminó con código $($process.ExitCode)." }
} finally {
    $env:LOCALAPPDATA = $previousLocalAppData
    $env:QT_QPA_PLATFORM = $previousQpa
}
if (-not (Test-Path -LiteralPath $report)) { throw "El ejecutable no generó el informe de smoke test." }
$smoke = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
if (-not $smoke.ok -or -not $smoke.frozen) { throw "El smoke test empaquetado no fue satisfactorio: $(Get-Content $report -Raw)" }
if ($smoke.data_directory -ne (Join-Path $smokeRoot "OrdenIA")) { throw "El ejecutable no utilizó LOCALAPPDATA." }

$size = (Get-ChildItem -LiteralPath (Split-Path $exe) -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host "OrdenIA.exe: $exe"
Write-Host ("Distribución: {0:N2} MB" -f ($size / 1MB))
Write-Host "FTS5 compilado: $($smoke.fts5_compile_option); repositorio FTS5: $($smoke.fts5_repository)"
Write-Host "Datos del smoke test: $($smoke.data_directory)"

if (-not $SkipInstaller) {
    $isccCandidates = @(@(
        (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) })
    if ($isccCandidates.Count -gt 0) {
        & $isccCandidates[0] "/DMyAppVersion=$version" (Join-Path $projectRoot "packaging\installer\ordenia.iss")
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup falló." }
        Write-Host "Instalador generado en dist\installer\OrdenIA-Setup-$version.exe"
    } else {
        Write-Host "Inno Setup 6 no está instalado. El ejecutable es válido; instala Inno Setup y repite para crear el instalador."
    }
}
