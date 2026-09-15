param(
    [Parameter(Mandatory = $false)]
    [ValidateSet("on", "off")]
    [string]$Action = "on",

    [Parameter(Mandatory = $false)]
    [string]$InterfaceAlias = ""
)

# Auto-elevate
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")) {
    Write-Host "Not admin, requesting elevation..." -ForegroundColor Yellow
    Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Action '$Action' -InterfaceAlias '$InterfaceAlias'" -Verb RunAs
    exit
}
Write-Host "Elevated successfully." -ForegroundColor Green

# Static IP settings
$StaticIP      = "172.17.174.5"
$PrefixLength  = 16
$StaticGateway = "172.17.0.1"
$DnsPrimary    = "8.8.8.8"
$DnsSecondary  = "114.114.114.114"

# Determine target adapter
if ([string]::IsNullOrWhiteSpace($InterfaceAlias)) {
    Write-Host "No adapter specified, auto-selecting..." -ForegroundColor Cyan

    # Try adapter named 'AAAA' first
    Write-Host "Looking for adapter named 'AAAA' (connected)..." -ForegroundColor DarkYellow
    $ethAdapters = @(Get-NetAdapter -Name "AAAA" -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq "Up" })
    if ($ethAdapters.Count -gt 0) {
        $selectedAdapter = $ethAdapters[0]
        Write-Host "Found: $($selectedAdapter.Name) (Status: $($selectedAdapter.Status))" -ForegroundColor Green
    } else {
        Write-Host "'AAAA' not found or not connected, falling back to first connected adapter..." -ForegroundColor DarkYellow
        $adapters = @(Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq "Up" })
        if ($adapters.Count -eq 0) {
            Write-Host "Warning: no connected adapters, trying all available..." -ForegroundColor Yellow
            $adapters = @(Get-NetAdapter -ErrorAction SilentlyContinue)
        }
        if ($adapters.Count -eq 0) {
            Write-Host "ERROR: cannot get any network adapter, exiting." -ForegroundColor Red
            exit 1
        }
        $selectedAdapter = $adapters[0]
        Write-Host "Fallback selected: $($selectedAdapter.Name) (Status: $($selectedAdapter.Status))" -ForegroundColor Yellow
    }
    $InterfaceAlias = $selectedAdapter.Name
    Write-Host "Final adapter: $InterfaceAlias" -ForegroundColor Green
} else {
    Write-Host "Using manually specified adapter: $InterfaceAlias" -ForegroundColor Cyan
}

# Execute action
switch ($Action) {
    "on" {
        Write-Host "`n=== Configuring static IP ===" -ForegroundColor Magenta
        try {
            Write-Host "1. Removing existing IPv4 addresses..." -NoNewline
            Remove-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 -Confirm:$false -ErrorAction SilentlyContinue
            Write-Host " Done" -ForegroundColor Green

            Write-Host "2. Removing default route..." -NoNewline
            Remove-NetRoute -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 -DestinationPrefix "0.0.0.0/0" -Confirm:$false -ErrorAction SilentlyContinue
            Write-Host " Done" -ForegroundColor Green

            Write-Host "3. Disabling DHCP..." -NoNewline
            Set-NetIPInterface -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 -Dhcp Disabled -ErrorAction Stop
            Write-Host " Done" -ForegroundColor Green

            Write-Host "4. Setting static IP: $StaticIP/$PrefixLength, gateway: $StaticGateway..." -NoNewline
            New-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 `
                -IPAddress $StaticIP -PrefixLength $PrefixLength -DefaultGateway $StaticGateway -ErrorAction Stop
            Write-Host " Done" -ForegroundColor Green

            Write-Host "5. Setting DNS: $DnsPrimary, $DnsSecondary..." -NoNewline
            Set-DnsClientServerAddress -InterfaceAlias $InterfaceAlias -ServerAddresses "$DnsPrimary,$DnsSecondary" -ErrorAction Stop
            Write-Host " Done" -ForegroundColor Green

            Write-Host "6. Flushing DNS cache..." -NoNewline
            ipconfig /flushdns | Out-Null
            Write-Host " Done" -ForegroundColor Green

            Write-Host "`nSUCCESS: Static IP configured." -ForegroundColor Green
        }
        catch {
            Write-Host " FAILED!" -ForegroundColor Red
            Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
            Write-Host "Check adapter name or parameters." -ForegroundColor Yellow
        }
    }
    "off" {
        Write-Host "`n=== Restoring DHCP ===" -ForegroundColor Magenta
        try {
            Write-Host "1. Removing existing IPv4 addresses..." -NoNewline
            Remove-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 -Confirm:$false -ErrorAction SilentlyContinue
            Write-Host " Done" -ForegroundColor Green

            Write-Host "2. Enabling DHCP..." -NoNewline
            Set-NetIPInterface -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 -Dhcp Enabled -ErrorAction Stop
            Write-Host " Done" -ForegroundColor Green

            Write-Host "3. Resetting DNS to automatic..." -NoNewline
            Set-DnsClientServerAddress -InterfaceAlias $InterfaceAlias -ResetServerAddresses -ErrorAction Stop
            Write-Host " Done" -ForegroundColor Green

            Write-Host "4. Renewing IP address..." -NoNewline
            ipconfig /renew $InterfaceAlias | Out-Null
            Write-Host " Done" -ForegroundColor Green

            Write-Host "`nSUCCESS: DHCP restored." -ForegroundColor Green
        }
        catch {
            Write-Host " FAILED!" -ForegroundColor Red
            Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
        }
    }
}

# Show current configuration
Write-Host "`n=== Current network config ===" -ForegroundColor Cyan
Get-NetIPConfiguration -InterfaceAlias $InterfaceAlias | Format-List InterfaceAlias, IPv4Address, IPv4DefaultGateway, DnsServer