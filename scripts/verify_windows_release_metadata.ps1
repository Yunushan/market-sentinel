param(
    [Parameter(Mandatory = $true)][string]$ExecutablePath,
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$PythonExecutable
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$nativeVersion = & $PythonExecutable -c 'import sys; from scripts.build_windows_release import msi_product_version; print(msi_product_version(sys.argv[1]))' $Version
if ($LASTEXITCODE -ne 0 -or $nativeVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "Could not resolve the native Windows version for $Version."
}
$versionParts = @($nativeVersion.Split('.') | ForEach-Object { [int]$_ })
$executable = (Resolve-Path -LiteralPath $ExecutablePath -ErrorAction Stop).Path
$installerPath = (Resolve-Path -LiteralPath $InstallerPath -ErrorAction Stop).Path

$metadata = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($executable)
if (
    $metadata.ProductName -cne "MarketSentinel" -or
    $metadata.FileDescription -cne "MarketSentinel" -or
    $metadata.InternalName -cne "market-sentinel" -or
    $metadata.OriginalFilename -cne "market-sentinel.exe" -or
    $metadata.ProductVersion -cne $nativeVersion -or
    $metadata.FileVersion -cne $nativeVersion -or
    $metadata.ProductMajorPart -ne $versionParts[0] -or
    $metadata.ProductMinorPart -ne $versionParts[1] -or
    $metadata.ProductBuildPart -ne $versionParts[2] -or
    $metadata.ProductPrivatePart -ne 0 -or
    $metadata.FileMajorPart -ne $versionParts[0] -or
    $metadata.FileMinorPart -ne $versionParts[1] -or
    $metadata.FileBuildPart -ne $versionParts[2] -or
    $metadata.FilePrivatePart -ne 0
) {
    throw "The Windows executable version resource does not match the release's native product version $nativeVersion."
}

$windowsInstaller = New-Object -ComObject WindowsInstaller.Installer
$database = $windowsInstaller.OpenDatabase($installerPath, 0)
$view = $database.OpenView('SELECT `Property`, `Value` FROM `Property`')
$properties = @{}
try {
    $view.Execute()
    while ($null -ne ($record = $view.Fetch())) {
        $name = $record.StringData(1)
        if ($name -in @("ProductName", "ProductVersion")) {
            if ($properties.ContainsKey($name)) {
                throw "The Windows installer contains duplicate $name metadata."
            }
            $properties[$name] = $record.StringData(2)
        }
    }
} finally {
    $view.Close()
}
if ($properties["ProductName"] -cne "MarketSentinel" -or $properties["ProductVersion"] -cne $nativeVersion) {
    throw "The Windows installer product metadata does not match the executable and release version $nativeVersion."
}

Write-Output "[ok] Windows executable and MSI product metadata match $nativeVersion (release $Version)"
