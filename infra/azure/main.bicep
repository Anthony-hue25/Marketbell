// MarketBell on Azure Container Apps — resource-group scoped, parameterized, no secret values.
//
// Topology (see docs/AZURE_ARCHITECTURE_DECISION.md):
//   api     Container App, external HTTPS ingress, 1 replica   -> website + JSON API
//   worker  Container App, NO ingress, exactly 1 replica        -> news monitor + Nemotron jobs
//   anchor  Container Apps Job, daily                           -> audit verify + external head anchor
//   PostgreSQL Flexible Server (Burstable B1ms, private VNet access, Entra ID auth only)
//   Key Vault (RBAC) for Nebius / webhook / admin secrets; ACR Standard (free-account eligible); Log Analytics; alerts; budget.
//
// Two-phase deployment: deployApps=false creates the platform; secrets are then set in Key Vault and
// the PostgreSQL role for the app identity is created (runbook); deployApps=true deploys the apps.

targetScope = 'resourceGroup'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@allowed(['staging', 'production'])
param environmentName string

@description('Short lowercase prefix for resource names (3-10 chars).')
@minLength(3)
@maxLength(10)
param prefix string = 'mktbell'

@description('Deploy the container apps and job (phase 2). Requires secrets in Key Vault and an image in ACR.')
param deployApps bool = false

@description('Immutable image reference, e.g. <acr>.azurecr.io/marketbell@sha256:<digest>. Tags are rejected.')
param image string = ''

@allowed(['vnet', 'public'])
@description('vnet: PostgreSQL private access inside a VNet (recommended). public: PostgreSQL public endpoint limited to Azure services (lower isolation).')
param networkMode string = 'vnet'

@description('Object ID of the Entra user/group that administers PostgreSQL (used once to create the app role).')
param pgAdminObjectId string

@description('Display/UPN name of that Entra principal.')
param pgAdminPrincipalName string

@allowed(['User', 'Group', 'ServicePrincipal'])
param pgAdminPrincipalType string = 'User'

@description('Create/update the Entra database administrator. Set false after the first platform deployment: PostgreSQL stores role names truncated to 63 characters, so re-applying a longer principal name fails with "role already exists".')
param managePgAdmin bool = true

@description('Fresh Nemotron inference from the web workspace (corrections, ad-hoc investigations). Autonomous monitoring is unaffected.')
param allowInteractiveLive bool = false

@description('Public judge demo: discard personal holding details entered by anonymous visitors.')
param publicDemo bool = true

@description('Optional custom origin, e.g. https://marketbell.example.com. Empty = Container Apps default FQDN.')
param publicOrigin string = ''

@description('Daily/monthly model spend reservations (USD) enforced by the application.')
param dailyModelUsd string = '0.50'
param monthlyModelUsd string = '10'
param dailyModelCalls string = '60'
param interactiveDailyCalls string = '20'

@allowed(['continuous', 'scheduled'])
@description('continuous: always-on worker app (lowest latency, recommended). scheduled: Container Apps Job every 5 minutes (lowest cost; ~5-minute detection latency).')
param workerMode string = 'continuous'

@description('Contact for SEC EDGAR fair access, e.g. "MarketBell research you@example.com". Not a secret and not a login. Empty = SEC filings channel off.')
param secUserAgent string = ''

@allowed([5, 10, 15, 30, 60])
@description('scheduled mode only: minutes between worker runs. 60 = hourly (fewest wake-ups; news can wait up to an hour). Heartbeat age, news cadence and alert windows follow this value.')
param workerScheduleMinutes int = 5

var workerCron = workerScheduleMinutes == 60 ? '0 * * * *' : '*/${workerScheduleMinutes} * * * *'
var cadenceSeconds = workerMode == 'scheduled' ? workerScheduleMinutes * 60 : 300
var heartbeatMaxAge = workerMode == 'scheduled' ? string(workerScheduleMinutes * 60 + 600) : '120'
var stoppedWindow = workerMode == 'continuous' || workerScheduleMinutes <= 10 ? 'PT15M' : workerScheduleMinutes <= 15 ? 'PT45M' : workerScheduleMinutes <= 30 ? 'PT1H' : 'PT2H'
var staleSeconds = max(1800, cadenceSeconds * 2)

@description('API container size. 0.25 vCPU / 0.5 GiB serves the demo (measured ~13 ms CPU per state request); use 0.5 / 1Gi for many users.')
param apiCpu string = '0.25'
param apiMemory string = '0.5Gi'

@description('Worker container size (measured locally: 64-90 MiB RSS; CPU mostly waiting on the model provider).')
param workerCpu string = '0.25'
param workerMemory string = '0.5Gi'

@description('Parallel investigation slots in the single active worker.')
param workerConcurrency string = '2'

@description('Monthly Azure budget (USD) for this resource group; notifications only, not a spending cap.')
param monthlyBudgetUsd int = 40

@description('Email for alerts and budget notifications.')
param alertEmail string

param budgetStartDate string = utcNow('yyyy-MM-01')

@description('Log Analytics daily ingestion cap in GB (cost guard). If reached, the heartbeat alert fires.')
param logDailyCapGb string = '0.25'

var tags = {
  app: 'marketbell'
  environment: environmentName
  costCenter: 'marketbell-${environmentName}'
  managedBy: 'bicep'
}
var suffix = uniqueString(resourceGroup().id, environmentName)
var name = '${prefix}-${environmentName}'
var kvName = take('${prefix}${environmentName == 'production' ? 'prd' : 'stg'}${suffix}', 24)
var acrName = replace('${prefix}${suffix}acr', '-', '')
var pgName = '${name}-pg-${take(suffix, 6)}'
var saName = take(replace('${prefix}${suffix}anc', '-', ''), 24)
var appIdentityName = '${name}-app-id'
var dbName = 'marketbell'
var dbSchema = 'marketbell'
var apiName = '${name}-api'
var workerName = '${name}-worker'
var production = environmentName == 'production'

// ---------- Observability ----------
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${name}-logs'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: json(logDailyCapGb) }
    features: { enableLogAccessUsingOnlyResourcePermissions: true }
  }
}

// ---------- Identity ----------
resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: appIdentityName
  location: location
  tags: tags
}

// ---------- Registry ----------
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  tags: tags
  // Standard is the tier included in the Azure free account's 12-month services; Basic is not.
  sku: { name: 'Standard' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, appIdentity.id, 'acrpull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------- Secrets ----------
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 30
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
  }
}

resource vaultSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, appIdentity.id, 'kv-secrets-user')
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------- Network (vnet mode) ----------
resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = if (networkMode == 'vnet') {
  name: '${name}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.40.0.0/16'] }
    subnets: [
      {
        name: 'aca'
        properties: {
          addressPrefix: '10.40.0.0/23'
          delegations: [{ name: 'aca', properties: { serviceName: 'Microsoft.App/environments' } }]
        }
      }
      {
        name: 'postgres'
        properties: {
          addressPrefix: '10.40.2.0/28'
          delegations: [{ name: 'pg', properties: { serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers' } }]
        }
      }
    ]
  }
}

resource pgDns 'Microsoft.Network/privateDnsZones@2020-06-01' = if (networkMode == 'vnet') {
  name: '${pgName}.private.postgres.database.azure.com'
  location: 'global'
  tags: tags
}

resource pgDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = if (networkMode == 'vnet') {
  parent: pgDns
  name: '${name}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnet.id }
  }
}

// ---------- Database ----------
resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: pgName
  location: location
  tags: tags
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '16'
    storage: { storageSizeGB: 32, autoGrow: 'Enabled' }
    backup: { backupRetentionDays: production ? 14 : 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Disabled'
      tenantId: subscription().tenantId
    }
    network: networkMode == 'vnet' ? {
      publicNetworkAccess: 'Disabled'
      delegatedSubnetResourceId: '${vnet.id}/subnets/postgres'
      privateDnsZoneArmResourceId: pgDns.id
    } : {
      publicNetworkAccess: 'Enabled'
    }
  }
  dependsOn: [pgDnsLink]
}

resource pgAdmin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2024-08-01' = if (managePgAdmin) {
  parent: pg
  name: pgAdminObjectId
  properties: {
    principalName: pgAdminPrincipalName
    principalType: pgAdminPrincipalType
    tenantId: subscription().tenantId
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: pg
  name: dbName
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

resource pgAzureServices 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = if (networkMode == 'public') {
  parent: pg
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

// ---------- External audit anchors (write-once) ----------
resource anchors 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: saName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
  }
}

resource anchorBlobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: anchors
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 30 }
  }
}

resource anchorContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: anchorBlobs
  name: 'audit-anchors'
  properties: { publicAccess: 'None' }
}

// Unlocked time-based retention: anchors cannot be modified or deleted for 365 days. Locking the policy
// is irreversible and is left as an explicit owner decision (runbook).
resource anchorRetention 'Microsoft.Storage/storageAccounts/blobServices/containers/immutabilityPolicies@2023-05-01' = {
  parent: anchorContainer
  name: 'default'
  properties: {
    immutabilityPeriodSinceCreationInDays: 365
    allowProtectedAppendWrites: false
  }
}

resource anchorWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(anchorContainer.id, appIdentity.id, 'blob-contributor')
  scope: anchorContainer
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------- Container Apps environment (Consumption profile only; no dedicated plan fee) ----------
resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${name}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    vnetConfiguration: networkMode == 'vnet' ? {
      infrastructureSubnetId: '${vnet.id}/subnets/aca'
      internal: false
    } : null
    zoneRedundant: false
  }
}

var origin = empty(publicOrigin) ? 'https://${apiName}.${env.properties.defaultDomain}' : publicOrigin
var databaseUrl = 'postgresql://${appIdentityName}@${pg.properties.fullyQualifiedDomainName}:5432/${dbName}?sslmode=require'
var registry = [{ server: acr.properties.loginServer, identity: appIdentity.id }]
var identity = {
  type: 'UserAssigned'
  userAssignedIdentities: { '${appIdentity.id}': {} }
}
var commonEnv = [
  { name: 'MARKETBELL_ENV', value: production ? 'production' : 'staging' }
  { name: 'MARKETBELL_DATABASE_URL', value: databaseUrl }
  { name: 'MARKETBELL_DATABASE_AUTH', value: 'managed-identity' }
  { name: 'MARKETBELL_DB_SCHEMA', value: dbSchema }
  { name: 'AZURE_CLIENT_ID', value: appIdentity.properties.clientId }
  { name: 'MARKETBELL_STRUCTURED_LOGS', value: '1' }
  { name: 'MARKETBELL_DAILY_USD', value: dailyModelUsd }
  { name: 'MARKETBELL_MONTHLY_USD', value: monthlyModelUsd }
  { name: 'MARKETBELL_DAILY_CALLS', value: dailyModelCalls }
  { name: 'MARKETBELL_INTERACTIVE_DAILY_CALLS', value: interactiveDailyCalls }
  { name: 'SEC_USER_AGENT', value: secUserAgent }
]
var kvSecret = 'https://${vault.name}${environment().suffixes.keyvaultDns}/secrets/'

resource api 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: apiName
  location: location
  tags: union(tags, { role: 'api' })
  identity: identity
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
        traffic: [{ latestRevision: true, weight: 100 }]
      }
      registries: registry
      secrets: [
        { name: 'webhook-token', keyVaultUrl: '${kvSecret}webhook-token', identity: appIdentity.id }
        { name: 'admin-token', keyVaultUrl: '${kvSecret}admin-token', identity: appIdentity.id }
      ]
    }
    template: {
      containers: [{
        name: 'api'
        image: image
        resources: { cpu: json(apiCpu), memory: apiMemory }
        env: concat(commonEnv, [
          { name: 'MARKETBELL_ROLE', value: 'api' }
          { name: 'MARKETBELL_WORKER', value: '0' }
          { name: 'MARKETBELL_PUBLIC_ORIGIN', value: origin }
          { name: 'MARKETBELL_TRUSTED_PROXY_HOPS', value: '1' }
          { name: 'MARKETBELL_ALLOW_LIVE', value: allowInteractiveLive ? '1' : '0' }
          { name: 'MARKETBELL_PUBLIC_DEMO', value: publicDemo ? '1' : '0' }
          { name: 'MARKETBELL_WORKER_HEARTBEAT_MAX_AGE', value: heartbeatMaxAge }
          { name: 'MARKETBELL_WEBHOOK_TOKEN', secretRef: 'webhook-token' }
          { name: 'MARKETBELL_ADMIN_TOKEN', secretRef: 'admin-token' }
        ])
        probes: [
          { type: 'Startup', httpGet: { path: '/livez', port: 8080 }, initialDelaySeconds: 2, periodSeconds: 3, failureThreshold: 30 }
          { type: 'Liveness', httpGet: { path: '/livez', port: 8080 }, periodSeconds: 30, failureThreshold: 3 }
          { type: 'Readiness', httpGet: { path: '/healthz', port: 8080 }, periodSeconds: 15, failureThreshold: 3 }
        ]
      }]
      // One replica: in-memory request rate limits stay coherent; spend limits are database-enforced.
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [acrPull, vaultSecretsUser]
}

resource worker 'Microsoft.App/containerApps@2024-03-01' = if (deployApps && workerMode == 'continuous') {
  name: workerName
  location: location
  tags: union(tags, { role: 'worker' })
  identity: identity
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: registry
      secrets: [
        { name: 'nebius-api-key', keyVaultUrl: '${kvSecret}nebius-api-key', identity: appIdentity.id }
      ]
    }
    template: {
      terminationGracePeriodSeconds: 60
      containers: [{
        name: 'worker'
        image: image
        command: ['python', '-m', 'backend.worker_main']
        resources: { cpu: json(workerCpu), memory: workerMemory }
        env: concat(commonEnv, [
          { name: 'MARKETBELL_ROLE', value: 'worker' }
          { name: 'MARKETBELL_ALLOW_LIVE', value: '1' }
          { name: 'MARKETBELL_WATCH', value: '1' }
          { name: 'MARKETBELL_SCHEDULER', value: '1' }
          { name: 'MARKETBELL_WATCH_SECONDS', value: '300' }
          { name: 'MARKETBELL_WORKER_CONCURRENCY', value: workerConcurrency }
          { name: 'MARKETBELL_SHUTDOWN_GRACE_SECONDS', value: '45' }
          { name: 'MARKETBELL_HEALTH_PORT', value: '8081' }
          { name: 'NEBIUS_API_KEY', secretRef: 'nebius-api-key' }
        ])
        probes: [
          { type: 'Startup', httpGet: { path: '/livez', port: 8081 }, initialDelaySeconds: 3, periodSeconds: 5, failureThreshold: 60 }
          { type: 'Liveness', httpGet: { path: '/livez', port: 8081 }, periodSeconds: 30, failureThreshold: 3 }
          { type: 'Readiness', httpGet: { path: '/healthz', port: 8081 }, periodSeconds: 30, failureThreshold: 5 }
        ]
      }]
      // Exactly one always-on replica: monitoring must continue with no browser open. Leases make a
      // brief overlap during revision rollover safe (the newcomer stays standby until the lease frees).
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [acrPull, vaultSecretsUser]
}

var workerEnv = concat(commonEnv, [
  { name: 'MARKETBELL_ROLE', value: 'worker' }
  { name: 'MARKETBELL_ALLOW_LIVE', value: '1' }
  { name: 'MARKETBELL_WATCH', value: '1' }
  { name: 'MARKETBELL_SCHEDULER', value: '1' }
  { name: 'MARKETBELL_WATCH_SECONDS', value: string(cadenceSeconds) }
  { name: 'MARKETBELL_WORKER_CONCURRENCY', value: workerConcurrency }
  { name: 'MARKETBELL_RUN_ONCE_SECONDS', value: '240' }
  { name: 'NEBIUS_API_KEY', secretRef: 'nebius-api-key' }
])

// Scheduled alternative: one bounded pass every 5 minutes; leases prevent overlapping runs.
resource workerJob 'Microsoft.App/jobs@2024-03-01' = if (deployApps && workerMode == 'scheduled') {
  name: '${name}-worker-scan'
  location: location
  tags: union(tags, { role: 'worker' })
  identity: identity
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 290
      replicaRetryLimit: 0
      scheduleTriggerConfig: { cronExpression: workerCron, parallelism: 1, replicaCompletionCount: 1 }
      registries: registry
      secrets: [
        { name: 'nebius-api-key', keyVaultUrl: '${kvSecret}nebius-api-key', identity: appIdentity.id }
      ]
    }
    template: {
      containers: [{
        name: 'worker'
        image: image
        command: ['python', '-m', 'backend.worker_main', '--once']
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: workerEnv
      }]
    }
  }
  dependsOn: [acrPull, vaultSecretsUser]
}

resource anchorJob 'Microsoft.App/jobs@2024-03-01' = if (deployApps) {
  name: '${name}-anchor'
  location: location
  tags: union(tags, { role: 'audit-anchor' })
  identity: identity
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 900
      replicaRetryLimit: 1
      scheduleTriggerConfig: { cronExpression: '17 3 * * *', parallelism: 1, replicaCompletionCount: 1 }
      registries: registry
    }
    template: {
      containers: [{
        name: 'anchor'
        image: image
        command: ['python', '-m', 'scripts.audit_anchor']
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: concat(commonEnv, [
          { name: 'MARKETBELL_ROLE', value: 'anchor' }
          { name: 'MARKETBELL_ANCHOR_CONTAINER_URL', value: '${anchors.properties.primaryEndpoints.blob}audit-anchors' }
        ])
      }]
    }
  }
  dependsOn: [acrPull, anchorWriter]
}

// ---------- Alerts (log search, 15-minute frequency) ----------
resource actions 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: '${name}-alerts'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: take('mb${environmentName}', 12)
    enabled: true
    emailReceivers: [{ name: 'owner', emailAddress: alertEmail, useCommonAlertSchema: true }]
  }
}

var events = 'ContainerAppConsoleLogs_CL | extend d = parse_json(Log_s) | where tostring(d.service) == "marketbell"'
var alertRules = [
  { key: 'monitoring-stopped', sev: 1, window: stoppedWindow, op: 'LessThan', threshold: 1
    title: 'MarketBell monitoring stopped (no worker heartbeat within one run window)'
    query: '${events} | where tostring(d.event) == "worker.heartbeat" | summarize n = count()', metric: 'n' }
  { key: 'news-stale', sev: 2, window: stoppedWindow, op: 'GreaterThan', threshold: staleSeconds
    title: 'News sources have not answered successfully for two run intervals'
    query: '${events} | where tostring(d.event) == "worker.heartbeat" | summarize v = min(toint(d.news_freshness_seconds))', metric: 'v' }
  { key: 'queue-backlog', sev: 2, window: stoppedWindow, op: 'GreaterThan', threshold: staleSeconds
    title: 'Investigation backlog older than 30 minutes'
    query: '${events} | where tostring(d.event) == "worker.heartbeat" | summarize v = max(toint(d.oldest_pending_seconds))', metric: 'v' }
  { key: 'model-failures', sev: 2, window: 'PT1H', op: 'GreaterThan', threshold: 5
    title: 'Rising Nemotron failures'
    query: '${events} | where tostring(d.event) == "model.call" and tostring(d.status) == "failed" | summarize n = count()', metric: 'n' }
  { key: 'spend', sev: 2, window: 'PT1H', op: 'GreaterThan', threshold: 0
    title: 'Model spend reached 80% of the daily application limit'
    query: '${events} | where tostring(d.event) == "worker.heartbeat" | summarize v = max(todouble(d.estimated_spend_today_usd)) - 0.8 * ${dailyModelUsd}', metric: 'v' }
  { key: 'security', sev: 2, window: 'PT15M', op: 'GreaterThan', threshold: 300
    title: 'Unusual volume of rejected or rate-limited requests'
    query: '${events} | where tostring(d.event) == "security.denied" | summarize n = count()', metric: 'n' }
  { key: 'integrity', sev: 0, window: 'P1D', op: 'GreaterThan', threshold: 0
    title: 'Audit or data integrity check failed'
    query: '${events} | where tostring(d.event) in ("audit.integrity_failed", "audit.anchor_failed") | summarize n = count()', metric: 'n' }
  { key: 'holding-deferred', sev: 3, window: 'PT1H', op: 'GreaterThan', threshold: 0
    title: 'Holding news deferred by exhausted AI budget'
    query: '${events} | where tostring(d.event) == "budget.deferred" | summarize n = count()', metric: 'n' }
]

resource rules 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = [for r in alertRules: if (deployApps) {
  name: '${name}-${r.key}'
  location: location
  tags: tags
  properties: {
    displayName: r.title
    severity: r.sev
    enabled: true
    scopes: [logs.id]
    evaluationFrequency: 'PT15M'
    windowSize: r.window
    criteria: {
      allOf: [{
        query: r.query
        timeAggregation: 'Maximum'
        metricMeasureColumn: r.metric
        operator: r.op
        threshold: r.threshold
        failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
      }]
    }
    autoMitigate: true
    // The container log table appears only after the apps first write logs, so the queries cannot be
    // validated on the first deployment.
    skipQueryValidation: true
    actions: { actionGroups: [actions.id] }
  }
}]

// ---------- Budget (notification only; Azure budgets do not stop spending) ----------
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: '${name}-monthly'
  properties: {
    category: 'Cost'
    amount: monthlyBudgetUsd
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStartDate }
    notifications: {
      actual50: { enabled: true, operator: 'GreaterThan', threshold: 50, contactEmails: [alertEmail], thresholdType: 'Actual' }
      actual80: { enabled: true, operator: 'GreaterThan', threshold: 80, contactEmails: [alertEmail], thresholdType: 'Actual' }
      forecast100: { enabled: true, operator: 'GreaterThan', threshold: 100, contactEmails: [alertEmail], thresholdType: 'Forecasted' }
    }
  }
}

output acrLoginServer string = acr.properties.loginServer
output keyVaultName string = vault.name
output postgresServer string = pg.properties.fullyQualifiedDomainName
output appIdentityName string = appIdentity.name
output appIdentityClientId string = appIdentity.properties.clientId
output publicOrigin string = origin
output anchorContainerUrl string = '${anchors.properties.primaryEndpoints.blob}audit-anchors'
