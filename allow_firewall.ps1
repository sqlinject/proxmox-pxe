# Run in an elevated PowerShell. Replaces the auto-created "Block" rules for python.exe
# with Allow rules covering the PXE server's ports (proxyDHCP, TFTP incl. data ports, HTTP).
$py = (Get-Command python).Source   # or set the full path to the python.exe you run the server with
Get-NetFirewallApplicationFilter -Program $py | Get-NetFirewallRule | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'PXE server (UDP)' -Direction Inbound -Program $py -Protocol UDP -Action Allow -Profile Any | Out-Null
New-NetFirewallRule -DisplayName 'PXE server (HTTP 8080)' -Direction Inbound -Program $py -Protocol TCP -LocalPort 8080 -Action Allow -Profile Any | Out-Null
Get-NetFirewallApplicationFilter -Program $py | Get-NetFirewallRule | Format-Table DisplayName, Action, Profile -AutoSize
# To undo after the install: Get-NetFirewallRule -DisplayName 'PXE server*' | Remove-NetFirewallRule
