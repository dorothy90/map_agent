# Run from a developer's PowerShell with a normal, dedicated CPython installation.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Python)

$ErrorActionPreference = 'Stop'
function Confirm-Command([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed ($LASTEXITCODE)" }
}
$base = (& $Python -I -c "import sys; print(sys.base_prefix)").Trim()
Confirm-Command 'Inspect Python'
$analysis = $env:HARNESS_ISOLATION_ANALYSIS_VENV
if (-not $analysis) { $analysis = Join-Path $env:LOCALAPPDATA 'YieldHarness\analysis-venv' }
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$state = $env:HARNESS_ISOLATION_HOME
if (-not $state) { $state = Join-Path $env:LOCALAPPDATA 'YieldHarness\state' }

# Only the two Python distributions are shared read-only with restricted applications.
foreach ($path in @($base,$analysis)) {
    if ($path -notmatch '^[A-Za-z]:[\\/]') { throw "Use an absolute drive path for the Python runtime: $path" }
    $resolved = [IO.Path]::GetFullPath($path).TrimEnd('\')
    if ($resolved.Length -le 3 -or $resolved.StartsWith('\\')) { throw "Use a dedicated local Python directory" }
    foreach ($private in @($repo,$state,$env:USERPROFILE)) {
        $p = [IO.Path]::GetFullPath($private).TrimEnd('\')
        if ($resolved -ieq $p -or $p.StartsWith($resolved+'\',[StringComparison]::OrdinalIgnoreCase) -or
            ($private -ne $env:USERPROFILE -and $resolved.StartsWith($p+'\',[StringComparison]::OrdinalIgnoreCase))) {
            throw "Python runtime overlaps private application data: $resolved"
        }
    }
    for ($node=[IO.DirectoryInfo]::new($resolved); $node; $node=$node.Parent) {
        if ($node.Exists -and ($node.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Python runtime path crosses a reparse point: $node"
        }
    }
}
if (-not (Test-Path (Join-Path $base 'python.exe'))) { throw 'Use a full CPython installation' }
if (-not (Test-Path (Join-Path $analysis 'Scripts\python.exe'))) {
    if ((Test-Path $analysis) -and @(Get-ChildItem -Force $analysis).Count -gt 0) {
        throw "Choose an empty directory for the dedicated analysis venv"
    }
    & $Python -m venv $analysis
    Confirm-Command 'Create analysis venv'
}
$analysisPython = Join-Path $analysis 'Scripts\python.exe'
& $analysisPython -I -c "import os,sys; assert os.path.normcase(os.path.abspath(sys.prefix))==os.path.normcase(os.path.abspath(sys.argv[1])) and os.path.normcase(sys.base_prefix)==os.path.normcase(sys.argv[2])" $analysis $base
Confirm-Command 'Verify dedicated runtime'
& $analysisPython -m pip install pandas numpy plotly pillow openpyxl matplotlib
Confirm-Command 'Install analysis libraries'
foreach ($path in @($base,$analysis)) {
    & icacls.exe $path /grant '*S-1-15-2-1:(OI)(CI)(RX)'
    Confirm-Command 'Grant AppContainer read/execute access on Python'
}
Write-Output "Analysis environment: $analysis"
