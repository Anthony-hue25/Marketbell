<#
.SYNOPSIS
  Approval-gated MarketBell deployment helper for Windows PowerShell 5.1+ / PowerShell 7 (same steps as deploy.sh).
.EXAMPLE
  .\infra\azure\deploy.ps1 -Action preview  -Environment staging -ResourceGroup rg-marketbell-staging
  .\infra\azure\deploy.ps1 -Action platform -Environment staging -ResourceGroup rg-marketbell-staging
  .\infra\azure\deploy.ps1 -Action apps     -Environment staging -ResourceGroup rg-marketbell-staging -Image <acr>.azurecr.io/marketbell@sha256:<64 hex>
  .\infra\azure\deploy.ps1 -Action rollback -Environment staging -ResourceGroup rg-marketbell-staging -Image <previous image@sha256:...>
.NOTES
  Requires Azure CLI (az) signed in with `az login`, and `az bicep install`. Nothing is created by 'preview'.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateSet('preview','platform','apps','rollback')][string]$Action,
  [Parameter(Mandatory)][ValidateSet('staging','production')][string]$Environment,
  [Parameter(Mandatory)][string]$ResourceGroup,
  [string]$Image = ''
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$template = Join-Path $here 'main.bicep'
$params = Join-Path $here "$Environment.bicepparam"
$stamp = Get-Date -Format 'yyyyMMddHHmmss'
function Invoke-Az { param([string[]]$Arguments) & az @Arguments; if ($LASTEXITCODE -ne 0) { throw "az $($Arguments[0..2] -join ' ') failed ($LASTEXITCODE)" } }
if ($Action -in 'apps','rollback' -and $Image -notmatch '@sha256:[0-9a-f]{64}$') { throw 'Refusing: image must be pinned by digest (repo@sha256:...)' }
switch ($Action) {
  'preview' {
    $extra = @(); if ($Image) { $extra = @('-p','deployApps=true',"image=$Image") }
    Invoke-Az (@('deployment','group','what-if','-g',$ResourceGroup,'-f',$template,'-p',$params) + $extra)
  }
  'platform' { Invoke-Az @('deployment','group','create','-g',$ResourceGroup,'-f',$template,'-p',$params,'-p','deployApps=false','--name',"platform-$stamp") }
  default {
    Invoke-Az @('deployment','group','what-if','-g',$ResourceGroup,'-f',$template,'-p',$params,'-p','deployApps=true',"image=$Image")
    $ok = Read-Host "Apply to $Environment ($ResourceGroup) with $Image ? Type the environment name to confirm"
    if ($ok -ne $Environment) { Write-Host 'Not confirmed; nothing changed.'; exit 3 }
    Invoke-Az @('deployment','group','create','-g',$ResourceGroup,'-f',$template,'-p',$params,'-p','deployApps=true',"image=$Image",'--name',"$Action-$stamp")
  }
}
