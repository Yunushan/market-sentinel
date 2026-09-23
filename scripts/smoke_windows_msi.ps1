param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [Parameter(Mandatory = $true)][string]$StagedExecutablePath,
    [Parameter(Mandatory = $true)][string]$Version
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Installing this per-machine MSI is only appropriate on a disposable runner.
if ($env:GITHUB_ACTIONS -ne "true" -or
    $env:RUNNER_ENVIRONMENT -ne "github-hosted" -or
    $env:RUNNER_OS -ne "Windows" -or
    [string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
    throw "The MSI acceptance smoke requires a GitHub-hosted Windows runner."
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "The MSI acceptance smoke requires an administrator runner account."
}

$installer = (Resolve-Path -LiteralPath $InstallerPath).ProviderPath
$stagedExecutable = (Resolve-Path -LiteralPath $StagedExecutablePath).ProviderPath
$tempDirectory = (Resolve-Path -LiteralPath $env:RUNNER_TEMP).ProviderPath
$installLog = Join-Path $tempDirectory "market-sentinel-msi-install.log"
$uninstallLog = Join-Path $tempDirectory "market-sentinel-msi-uninstall.log"
$smokeDirectory = Join-Path $tempDirectory "market-sentinel-msi-smoke"
$programFilesRoots = @(
    $env:ProgramFiles
    [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique
$expectedInstallDirectories = @($programFilesRoots | ForEach-Object { Join-Path $_ "MarketSentinel" })

function Get-InstallMarkers {
    foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64, [Microsoft.Win32.RegistryView]::Registry32)) {
        $root = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $view)
        try {
            $key = $root.OpenSubKey("Software\market-sentinel")
            if ($null -ne $key) {
                try {
                    [pscustomobject]@{
                        View = $view
                        InstallDir = [string]$key.GetValue("InstallDir")
                    }
                } finally {
                    $key.Close()
                }
            }
        } finally {
            $root.Close()
        }
    }
}

function Invoke-MsiExec([string]$mode, [string]$logPath) {
    $arguments = @($mode, "`"$installer`"", "/qn", "/norestart", "/L*v", "`"$logPath`"")
    $process = Start-Process -FilePath (Join-Path $env:WINDIR "System32\msiexec.exe") `
        -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
    return $process.ExitCode
}

if (@(Get-InstallMarkers).Count -ne 0) {
    throw "A MarketSentinel installation registry marker already exists on this runner."
}
foreach ($directory in $expectedInstallDirectories) {
    if (Test-Path -LiteralPath $directory) {
        throw "A MarketSentinel installation directory already exists: $directory"
    }
}

$installAttempted = $false
$smokeFailure = $null
$cleanupFailure = $null
try {
    $installAttempted = $true
    $installExit = Invoke-MsiExec "/i" $installLog
    if ($installExit -ne 0) {
        throw "MSI install exited $installExit; see $installLog"
    }

    $markers = @(Get-InstallMarkers)
    if ($markers.Count -ne 1 -or [string]::IsNullOrWhiteSpace($markers[0].InstallDir)) {
        throw "The installed MSI did not create exactly one InstallDir registry marker."
    }
    $installDirectory = $markers[0].InstallDir.TrimEnd("\")
    if (-not ($expectedInstallDirectories | Where-Object { $_ -ieq $installDirectory })) {
        throw "The MSI installed outside the expected Program Files directory: $installDirectory"
    }

    $installedExecutable = Join-Path $installDirectory "market-sentinel.exe"
    $requiredFiles = @(
        $installedExecutable
        (Join-Path $installDirectory "VERSION.txt")
        (Join-Path $installDirectory "start_tkinter_gui.bat")
        (Join-Path $installDirectory "start_web_gui.bat")
        (Join-Path $installDirectory "frontend\dist\index.html")
    )
    foreach ($file in $requiredFiles) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            throw "Installed MSI payload is missing: $file"
        }
    }
    # Python's text writer uses CRLF on Windows; normalize only line endings.
    $installedVersionPath = Join-Path $installDirectory "VERSION.txt"
    $installedVersion = (Get-Content -LiteralPath $installedVersionPath -Raw).Replace("`r`n", "`n")
    if ($installedVersion -cne "market-sentinel $Version`n") {
        throw "Installed MSI version marker does not match ${Version}: $installedVersion"
    }
    $stagedVersionPath = Join-Path (Split-Path -Path $stagedExecutable -Parent) "VERSION.txt"
    if ((Get-FileHash -LiteralPath $installedVersionPath -Algorithm SHA256).Hash -cne
        (Get-FileHash -LiteralPath $stagedVersionPath -Algorithm SHA256).Hash) {
        throw "The installed version marker differs from the staged release version marker."
    }
    $stagedHash = (Get-FileHash -LiteralPath $stagedExecutable -Algorithm SHA256).Hash
    $installedHash = (Get-FileHash -LiteralPath $installedExecutable -Algorithm SHA256).Hash
    if ($installedHash -cne $stagedHash) {
        throw "The installed executable differs from the staged release executable."
    }

    New-Item -ItemType Directory -Path $smokeDirectory -Force | Out-Null
    # PyInstaller builds a windowed executable, so it has no stdout to parse.
    # Start-Process waits for that actual installed process and captures its exit code.
    $smokeProcess = Start-Process -FilePath $installedExecutable -ArgumentList "--smoke-test" `
        -WorkingDirectory $smokeDirectory -WindowStyle Hidden -PassThru
    if (-not $smokeProcess.WaitForExit(120000)) {
        Stop-Process -Id $smokeProcess.Id -Force
        throw "The installed executable smoke test timed out after two minutes."
    }
    if ($smokeProcess.ExitCode -ne 0) {
        throw "The installed executable smoke test exited $($smokeProcess.ExitCode)."
    }
    Write-Host "The installed MSI payload and executable smoke test passed."
} catch {
    $smokeFailure = $_
} finally {
    if ($installAttempted) {
        try {
            $uninstallExit = Invoke-MsiExec "/x" $uninstallLog
            if ($uninstallExit -notin @(0, 1605)) {
                throw "MSI uninstall exited $uninstallExit; see $uninstallLog"
            }
            if (@(Get-InstallMarkers).Count -ne 0) {
                throw "The MSI uninstall left the InstallDir registry marker behind."
            }
            foreach ($directory in $expectedInstallDirectories) {
                if (Test-Path -LiteralPath $directory) {
                    throw "The MSI uninstall left its Program Files directory behind: $directory"
                }
            }
            Write-Host "The MSI uninstall removed the installed payload and registry marker."
        } catch {
            $cleanupFailure = $_
        }
    }
}

if ($smokeFailure -or $cleanupFailure) {
    $messages = @()
    if ($smokeFailure) { $messages += "MSI acceptance failed: $($smokeFailure.Exception.Message)" }
    if ($cleanupFailure) { $messages += "MSI cleanup failed: $($cleanupFailure.Exception.Message)" }
    throw ($messages -join "; ")
}
