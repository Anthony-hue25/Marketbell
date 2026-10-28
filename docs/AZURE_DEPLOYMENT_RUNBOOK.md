# Azure deployment runbook (staging → production)

Nothing in this runbook has been executed against Azure. Every step that creates resources or incurs charges
requires the owner's explicit decision. Commands assume Azure CLI ≥ 2.60, `az bicep` and a bash shell
(PowerShell equivalents: replace line continuations `\` with `` ` ``).

## 0. Prerequisites (owner)

1. **Least privilege — no subscription Owner needed.** A subscription administrator does three one-time things:
   creates the resource group, registers the resource providers (step 4), and grants you on **that resource
   group only**: *Contributor* plus *Role Based Access Control Administrator* **constrained** to the four roles the
   template and runbook assign (AcrPull, Key Vault Secrets User, Storage Blob Data Contributor, Key Vault Secrets
   Officer):
   ```bash
   SCOPE=$(az group show -n rg-marketbell-staging --query id -o tsv)
   az role assignment create --assignee <you> --role Contributor --scope $SCOPE
   az role assignment create --assignee <you> --role "Role Based Access Control Administrator" --scope $SCOPE \
     --condition-version 2.0 --condition '((!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {7f951dda-4ed3-4680-a7ca-43fe172d538d, 4633458b-17de-408a-b874-0445c86b69e6, ba92f5b4-2d11-453d-a403-e96b0029c9fe, b86a8fe4-44ce-4948-aee5-eccb2c155cd7})) AND ((!(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'})) OR (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {7f951dda-4ed3-4680-a7ca-43fe172d538d, 4633458b-17de-408a-b874-0445c86b69e6, ba92f5b4-2d11-453d-a403-e96b0029c9fe, b86a8fe4-44ce-4948-aee5-eccb2c155cd7}))'
   ```
   Creating the GitHub app registration (step 3) needs Entra *Application Developer* (or an admin does it).
   No existing quotas are assumed.
   **Shell:** `infra/azure/deploy.ps1` (Windows PowerShell 5.1/7) and `infra/azure/deploy.sh` (bash, WSL, Git Bash,
   Cloud Shell) issue identical `az` calls (verified with a recording stub). Snippets below are bash; in PowerShell
   replace `$(…)` captures with `$x = az …` and line continuations `\` with `` ` ``.
2. Your Entra object ID: `az ad signed-in-user show --query id -o tsv` → `pgAdminObjectId`; UPN → `pgAdminPrincipalName`.
3. Edit `infra/azure/staging.bicepparam` (alert email, admin IDs). Do **not** put secret values in parameter files.
4. Register providers once: `for p in Microsoft.App Microsoft.DBforPostgreSQL Microsoft.OperationalInsights Microsoft.ContainerRegistry Microsoft.KeyVault Microsoft.Insights Microsoft.Network Microsoft.Storage Microsoft.Consumption; do az provider register -n $p; done`

## 1. Provision staging platform (phase 1, no apps)

```bash
RG=rg-marketbell-staging; LOC=eastus2
az group create -n $RG -l $LOC --tags app=marketbell environment=staging
./infra/azure/deploy.sh preview  staging $RG          # what-if: shows changes, applies nothing
./infra/azure/deploy.sh platform staging $RG          # creates platform resources (charges start: PostgreSQL, ACR, logs)
az deployment group show -g $RG -n <platform-deployment-name> --query properties.outputs
```

## 2. Identities, database role and secrets

```bash
KV=$(az keyvault list -g $RG --query [0].name -o tsv)
ME=$(az ad signed-in-user show --query id -o tsv)
az role assignment create --assignee $ME --role "Key Vault Secrets Officer" --scope $(az keyvault show -n $KV --query id -o tsv)
# Secrets (values typed or piped from your password manager; never echo them into logs or history)
az keyvault secret set --vault-name $KV --name nebius-api-key --file /path/to/nebius-key.txt
openssl rand -hex 32 | az keyvault secret set --vault-name $KV --name webhook-token --file /dev/stdin
openssl rand -hex 32 | az keyvault secret set --vault-name $KV --name admin-token   --file /dev/stdin
```

Create the PostgreSQL role for the app identity (Entra-only server; you are the Entra admin). In `vnet` mode
the server has no public endpoint, so run this from Cloud Shell attached to the VNet, a jump host, or
temporarily from the portal's *Connect* blade:

```sql
-- connect: psql "host=<server>.postgres.database.azure.com dbname=postgres user=<your UPN> sslmode=require"
--          with PGPASSWORD=$(az account get-access-token --resource-type oss-rdbms --query accessToken -o tsv)
SELECT * FROM pgaadauth_create_principal('mktbell-staging-app-id', false, false);
\c marketbell
CREATE SCHEMA IF NOT EXISTS marketbell AUTHORIZATION "mktbell-staging-app-id";
GRANT CONNECT, TEMPORARY, CREATE ON DATABASE marketbell TO "mktbell-staging-app-id";
-- CREATE is required: the app runs CREATE SCHEMA IF NOT EXISTS at start-up, and PostgreSQL checks the
-- database CREATE privilege before checking whether the schema exists.
```

The app creates its tables, functions and triggers inside schema `marketbell` on first start (migrations are
idempotent and serialized across containers).

## 3. Build, scan and push an immutable image

Preferred: the GitHub workflow (`.github/workflows/release.yml`) with OIDC. One-time federation setup:

```bash
APP=$(az ad app create --display-name marketbell-github --query appId -o tsv)
az ad sp create --id $APP
az ad app federated-credential create --id $APP --parameters '{"name":"main","issuer":"https://token.actions.githubusercontent.com","subject":"repo:<owner>/<repo>:environment:staging","audiences":["api://AzureADTokenExchange"]}'
az ad app federated-credential create --id $APP --parameters '{"name":"prod","issuer":"https://token.actions.githubusercontent.com","subject":"repo:<owner>/<repo>:environment:production","audiences":["api://AzureADTokenExchange"]}'
az role assignment create --assignee $APP --role Contributor --scope $(az group show -n $RG --query id -o tsv)
az role assignment create --assignee $APP --role AcrPush     --scope $(az acr list -g $RG --query [0].id -o tsv)
az role assignment create --assignee $APP --role "Role Based Access Control Administrator" --scope $(az group show -n $RG --query id -o tsv) \
  --condition-version 2.0 --condition '((!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {7f951dda-4ed3-4680-a7ca-43fe172d538d, 4633458b-17de-408a-b874-0445c86b69e6, ba92f5b4-2d11-453d-a403-e96b0029c9fe, b86a8fe4-44ce-4948-aee5-eccb2c155cd7})) AND ((!(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'})) OR (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {7f951dda-4ed3-4680-a7ca-43fe172d538d, 4633458b-17de-408a-b874-0445c86b69e6, ba92f5b4-2d11-453d-a403-e96b0029c9fe, b86a8fe4-44ce-4948-aee5-eccb2c155cd7}))'
```

Set repository variables `AZURE_CLIENT_ID=$APP`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RG_STAGING`,
`AZURE_RG_PRODUCTION`, `ACR_NAME`, `STAGING_ORIGIN`, `PRODUCTION_ORIGIN`; create GitHub environments `staging`
and `production` (production: required reviewers). No client secret is created.

Manual alternative:

```bash
ACR=$(az acr list -g $RG --query [0].name -o tsv)
az acr build -r $ACR -t marketbell:$(git rev-parse --short HEAD) .
DIGEST=$(az acr repository show -n $ACR --image marketbell:$(git rev-parse --short HEAD) --query digest -o tsv)
IMAGE=$ACR.azurecr.io/marketbell@$DIGEST
```

## 4. Deploy the apps (phase 2) and apply migrations

```bash
./infra/azure/deploy.sh apps staging $RG $IMAGE      # what-if, typed confirmation, then deploy
az containerapp logs show -g $RG -n mktbell-staging-worker --type console --tail 20   # worker.started / heartbeat
```

Migrations run automatically at container start (API and worker; serialized by an advisory lock).

## 5–10. Staging verification

```bash
ORIGIN=$(az containerapp show -g $RG -n mktbell-staging-api --query properties.configuration.ingress.fqdn -o tsv)
MARKETBELL_SMOKE_WEBHOOK_TOKEN=$(az keyvault secret show --vault-name $KV -n webhook-token --query value -o tsv) \
  python -m scripts.smoke https://$ORIGIN --wait-worker 300 --event-timeout 600      # 5 smoke + 6 test event
# 7. Nemotron: the smoke event runs Research + Challenge in the worker; check model.call events:
az monitor log-analytics query -w <workspace-id> --analytics-query 'ContainerAppConsoleLogs_CL | where Log_s has "model.call" | take 5'
# 8. Persistence: restart both apps and confirm the smoke case is still listed
az containerapp revision restart -g $RG -n mktbell-staging-worker --revision $(az containerapp revision list -g $RG -n mktbell-staging-worker --query [0].name -o tsv)
# 9. Worker restart: confirm a new worker.started event and fresh heartbeat (smoke check 'worker_heartbeat_fresh')
# 10. Monitoring: stop the worker for 20 minutes and confirm the 'monitoring stopped' alert e-mail, then start it:
REV=$(az containerapp revision list -g $RG -n mktbell-staging-worker --query [0].name -o tsv)
az containerapp revision deactivate -g $RG -n mktbell-staging-worker --revision $REV   # wait ~20 min for the alert
az containerapp revision activate   -g $RG -n mktbell-staging-worker --revision $REV
```

## 11. Rollback

Images are immutable digests; the previous digest is recorded by the pipeline (`rollback-reference` artifact) or
read before release:

```bash
PREV=$(az containerapp show -g $RG -n mktbell-staging-api --query 'properties.template.containers[0].image' -o tsv)  # before releasing
./infra/azure/deploy.sh rollback staging $RG $PREV
```

Schema changes are additive (`CREATE … IF NOT EXISTS`, new columns nullable), so a previous image runs against
a newer schema. Never roll back by restoring the database unless data is corrupt.

## 12. Restore from backup

```bash
az postgres flexible-server restore -g $RG --name mktbell-staging-pg-restore --source-server <server> --restore-time "2026-10-20T12:00:00Z"
# Point MARKETBELL_DATABASE_URL at the restored server (redeploy with a parameter override), recreate the app role,
# then run the integrity check against the restored copy:
az containerapp job start -g $RG -n mktbell-staging-anchor     # verifies the full chain and compares with the last anchor
```

The anchor job reports `ANCHORED_HISTORY_TRUNCATED_OR_REWRITTEN` if the restored history is older than the last
anchor — expected for a point-in-time restore; record the gap as an incident before re-anchoring.

## Production

Repeat steps 1–4 with `production.bicepparam` and resource group `rg-marketbell-production`, deploying the
**same digest that passed staging**, after explicit approval. Then: `python -m scripts.smoke https://<prod origin>`.

## Key rotation and recovery

| Secret | Rotate | Effect |
|---|---|---|
| `nebius-api-key` | Create a new key at Nebius → `az keyvault secret set …` → `az containerapp revision restart` (worker) → revoke old key | Worker only; in-flight case retries |
| `webhook-token` / `admin-token` | `openssl rand -hex 32 \| az keyvault secret set …` → restart API → update senders | API only |
| Database | No password (Entra tokens, ~1 h, refreshed per connection) | — |
| GitHub → Azure | Federated credentials; delete/recreate to revoke | No secret to leak |

Lost access to Key Vault: purge protection keeps deleted secrets for 30 days (`az keyvault secret recover`).
