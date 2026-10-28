using './main.bicep'

param environmentName = 'staging'
param prefix = 'mktbell'
param networkMode = 'vnet'
param deployApps = false              // phase 1; set true (with image) for phase 2
param image = ''                      // <acr>.azurecr.io/marketbell@sha256:<digest>
param pgAdminObjectId = '00000000-0000-0000-0000-000000000000'   // replace: your Entra object ID
param pgAdminPrincipalName = 'you@example.com'                    // replace
param alertEmail = 'you@example.com'                              // replace
param allowInteractiveLive = false
param publicDemo = true
param monthlyBudgetUsd = 25
param dailyModelUsd = '0.25'
param monthlyModelUsd = '5'
param secUserAgent = ''                // e.g. 'MarketBell research you@example.com' (free, no login)
