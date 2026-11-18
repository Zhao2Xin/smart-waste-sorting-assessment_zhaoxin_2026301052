[CmdletBinding()]
param(
    [ValidatePattern('^\d+-\d+(\.\d+)*$')]
    [string]$BusId,
    [ValidatePattern('^/dev/video\d+$')]
    [string]$VideoDevice
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$composeArgs = @('compose', '-f', (Join-Path $PSScriptRoot 'compose.yaml'),
    '-f', (Join-Path $PSScriptRoot 'compose.camera.yaml'))
$overridePath = $composeArgs[-1]

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments, [string]$Failure)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Failure (exit code $LASTEXITCODE)."
    }
}

function Find-Usbipd {
    $command = Get-Command usbipd.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $installed = Join-Path $env:ProgramFiles 'usbipd-win\usbipd.exe'
    if (Test-Path -LiteralPath $installed -PathType Leaf) { return $installed }
}

function Get-UsbDevices {
    $state = Invoke-Checked $usbipd @('state') 'Cannot read USB device state. Check the usbipd service and use usbipd-win 5 or newer'
    ($state | Out-String | ConvertFrom-Json).Devices | Where-Object { $_.BusId }
}

function Find-VideoNodes {
    # For UVC, index 0 is the capture node and index 1 is normally metadata.
    $probe = @'
for node in /sys/class/video4linux/video*; do
    [ -c "/dev/${node##*/}" ] || continue
    [ "$(cat "$node/index")" = 0 ] || continue
    usb=$(readlink -f "$node/device")
    while [ -n "$usb" ] && [ "$usb" != / ]; do
        if [ -r "$usb/idVendor" ] && [ -r "$usb/idProduct" ]; then
            [ "$(cat "$usb/idVendor"):$(cat "$usb/idProduct")" = '@USB_ID@' ] &&
                echo "/dev/${node##*/}"
            break
        fi
        usb=${usb%/*}
    done
done
exit 0
'@
    $probe = $probe.Replace('@USB_ID@', $usbId).Replace("`r", '')
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($probe))
    $nodes = Invoke-Checked wsl.exe @('-d', 'docker-desktop', '--', 'sh', '-c', "echo $encoded | base64 -d | sh") 'Cannot inspect video devices in docker-desktop WSL'
    $nodes | Where-Object { $_ -match '^/dev/video\d+$' }
}

function New-CameraOverride {
    param([string]$Device)
    @"
services:
  app:
    devices:
      - "${Device}:/dev/video0"
"@
}

Push-Location -LiteralPath $PSScriptRoot
try {
    $usbipd = Find-Usbipd
    if (-not $usbipd) {
        Write-Host 'Installing usbipd-win with WinGet...'
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $winget) {
            throw 'Install usbipd-win from https://github.com/dorssel/usbipd-win/releases, then run this script again.'
        }
        Invoke-Checked $winget.Source @('install', '--interactive', '--exact', '--id', 'dorssel.usbipd-win', '--source', 'winget', '--accept-package-agreements', '--accept-source-agreements') 'WinGet could not install usbipd-win'
        $usbipd = Find-Usbipd
        if (-not $usbipd) { throw 'Open a new PowerShell window and run the script again to pick up usbipd.' }
    }

    $devices = @(Get-UsbDevices)
    if ($BusId) {
        $cameras = @($devices | Where-Object BusId -eq $BusId)
    } else {
        $cameras = @($devices | Where-Object Description -match 'camera|webcam|\u6444\u50cf\u5934|\u651d\u50cf\u982d')
    }
    if ($cameras.Count -ne 1) {
        & $usbipd list
        throw 'Could not select one camera. Use usbipd list, then run this script with -BusId <busid>.'
    }
    $camera = $cameras[0]
    $BusId = $camera.BusId
    if ($camera.InstanceId -notmatch 'VID_([0-9a-f]{4})&PID_([0-9a-f]{4})') {
        throw "Could not read the USB hardware ID for $BusId."
    }
    $usbId = ('{0}:{1}' -f $Matches[1], $Matches[2]).ToLowerInvariant()
    Write-Host "Using USB device $BusId ($($camera.Description))."

    Invoke-Checked docker @('compose', 'version') 'Install Docker Desktop with the WSL 2 backend enabled' | Out-Host
    Invoke-Checked wsl.exe @('-d', 'docker-desktop', '--', 'true') 'Start Docker Desktop with the WSL 2 backend enabled'

    if (-not $camera.PersistedGuid) {
        Write-Host 'Sharing the camera. Approve the Windows administrator prompt if shown.'
        $bind = Start-Process -FilePath $usbipd -ArgumentList 'bind', '--busid', $BusId -Verb RunAs -WindowStyle Hidden -Wait -PassThru
        if ($bind.ExitCode -ne 0) { throw "usbipd bind failed (exit code $($bind.ExitCode)). Close Windows apps using the camera and retry." }
    }
    if (-not $camera.ClientIPAddress) {
        Write-Host 'Attaching the camera to WSL...'
        Invoke-Checked $usbipd @('attach', '--wsl', '--busid', $BusId) 'Could not attach the camera. Close Windows apps using it and retry'
    }

    $nodes = @(Find-VideoNodes)
    if (-not $nodes) {
        Write-Host 'Loading the UVC driver and waiting for the camera device...'
        Invoke-Checked wsl.exe @('-d', 'docker-desktop', '-u', 'root', '--', 'modprobe', 'uvcvideo') 'Cannot load uvcvideo in WSL. Check the WSL kernel and UVC support'
        for ($attempt = 0; $attempt -lt 20 -and -not $nodes; $attempt++) {
            Start-Sleep -Milliseconds 500
            $nodes = @(Find-VideoNodes)
        }
    }
    if (-not $nodes) { throw "No UVC capture node for $usbId. Check 'usbipd list' and the WSL kernel log before retrying." }
    if (-not $VideoDevice -and $nodes.Count -eq 1) { $VideoDevice = $nodes[0] }
    if ($VideoDevice -notin $nodes) {
        throw "Choose a capture node with -VideoDevice. Available for ${BusId}: $($nodes -join ', ')."
    }

    if (Test-Path -LiteralPath $overridePath) {
        $config = Invoke-Checked docker ($composeArgs + @('config', '--format', 'json')) 'Cannot parse existing Compose configuration'
        $merged = $config | Out-String | ConvertFrom-Json
        $mapping = $merged.services.app.devices | Where-Object target -eq '/dev/video0' | Select-Object -First 1
        if ($mapping.source -ne $VideoDevice) {
            $original = (Get-Content -LiteralPath $overridePath -Raw).Replace("`r", '').Trim()
            $standard = (New-CameraOverride $mapping.source).Replace("`r", '').Trim()
            if ($original -ne $standard) {
                throw "Custom Compose override detected. Set app.devices to map ${VideoDevice}:/dev/video0 in $overridePath and rerun."
            }
            New-CameraOverride $VideoDevice | Set-Content -LiteralPath $overridePath -Encoding ASCII
        }
    } else {
        New-CameraOverride $VideoDevice | Set-Content -LiteralPath $overridePath -Encoding ASCII
    }
    Invoke-Checked docker ($composeArgs + @('config', '--quiet')) 'Cannot validate the merged Compose configuration'
    Write-Host "Configured ${VideoDevice} -> /dev/video0 in compose.camera.yaml. Build and start the project manually when ready."
} finally {
    Pop-Location
}
