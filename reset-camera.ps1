$ErrorActionPreference = 'Stop'
$cameraBusId = '4-2'
$cameraHardwareId = '30c9:011d'
$usbipd = (Get-Command usbipd.exe -CommandType Application).Source
$cameraPattern = '^' + [regex]::Escape($cameraBusId) + '\s+' + [regex]::Escape($cameraHardwareId) + '\s+'

function Get-CameraRow {
    # Windows may take a moment to enumerate the camera after detach.
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $devices = & $usbipd list
        if ($LASTEXITCODE -ne 0) {
            throw "usbipd list failed (exit code $LASTEXITCODE)."
        }

        $row = $devices | Where-Object { $_ -match $cameraPattern } | Select-Object -First 1
        if ($row) {
            return $row
        }
        Start-Sleep -Milliseconds 250
    }

    throw "HP camera ($cameraHardwareId) not found at $cameraBusId. Run 'usbipd list' to check its current BUSID."
}

$cameraRow = Get-CameraRow
if ($cameraRow -match '\s+Attached\s*$') {
    & $usbipd detach --busid $cameraBusId
    if ($LASTEXITCODE -ne 0) {
        throw "usbipd detach failed (exit code $LASTEXITCODE)."
    }
    $cameraRow = Get-CameraRow
}

if ($cameraRow -match '\s+Shared(?:\s+\(forced\))?\s*$' -and $cameraRow -notmatch '\s+Not shared\s*$') {
    $unbind = Start-Process -FilePath $usbipd -ArgumentList 'unbind', '--busid', $cameraBusId `
        -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    if ($unbind.ExitCode -ne 0) {
        throw "usbipd unbind failed (exit code $($unbind.ExitCode))."
    }
    $cameraRow = Get-CameraRow
}

if ($cameraRow -notmatch '\s+Not shared\s*$') {
    throw "Expected Not shared, but usbipd reported: $cameraRow"
}

Write-Output $cameraRow
