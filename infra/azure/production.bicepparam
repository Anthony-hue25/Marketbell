using './main.bicep'

param environmentName = 'production'
param prefix = 'mktbell'
param networkMode = 'vnet'
param deployApps = false
param image = ''
param pgAdminObjectId = '00000000-0000-0000-0000-000000000000'
param pgAdminPrincipalName = 'you@example.com'
param alertEmail = 'you@example.com'
param allowInteractiveLive = false
param publicDemo = true
param monthlyBudgetUsd = 40
param dailyModelUsd = '0.50'
param monthlyModelUsd = '10'
param workerConcurrency = '2'
param secUserAgent = ''                // e.g. 'MarketBell research you@example.com' (free, no login)
