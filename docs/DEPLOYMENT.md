# Deployment and rollback

Status: prepared, not deployed. No existing production service, personal credential or private source has been published. The installed Docker CLI cannot connect to a Linux engine; `docker desktop start --detach` also fails because the Desktop registry installation is missing. Container build and runtime have not been verified on this host.

## Approved Linux host procedure

1. Confirm approval of the host/domain, infrastructure budget, public source publication and replay data redistribution. Confirm ownership/licensing of copied internal source; the original repositories contain no detected license file. Do not assume a public redistribution license.
2. On the approved host, install Docker with Compose using the host operator's approved process. Build this checkout: `docker build -t marketbell:release .`. Record the resulting immutable image digest and commit SHA. No image push or paid service provisioning has been done.
3. Copy `.env.production.example` to the ignored `.env.production`. Set `MARKETBELL_PUBLIC_ORIGIN` to the exact HTTPS origin. Initially leave live inference, continuous monitoring and scheduled baseline processing disabled. Replay contains genuine captured calls and works without keys.
4. Create a persistent Docker volume using `docker compose up -d --build`. The app binds on host loopback port 8080. Configure a dedicated approved TLS reverse proxy/domain to that port. Do not expose the development WSGI server. The container uses Gunicorn, one worker process, four threads, a non-root user, read-only filesystem and a writable `/data` volume.
5. Verify `/healthz`, the full judge walkthrough, cookie Secure/HttpOnly/SameSite flags, hostile Origin rejection and isolation between two clean browser sessions. Run container health inspection. This verification is required before declaring release.
6. To enable fresh inference, inject a **dedicated approved deployment** `NEBIUS_API_KEY` or mount a dedicated secret file and set `NEBIUS_KEY_FILE`. Never bake or copy the existing personal key into an image or public environment. Set `MARKETBELL_ALLOW_LIVE=1`, keep a daily call and estimated-spend budgets, and recreate the service. Public inference is intentionally off in the template.
7. To enable the scheduler, supply authorized market-data-only Alpaca process credentials, or mount a validated authorized data cache read-only. Set `MARKETBELL_WATCH=1` for continuous event detection and `MARKETBELL_SCHEDULER=1` for scheduled completed-session context. Configure a separate webhook secret for authorized external event sources. A private admin bearer token can be supplied via the deployment secret manager; no admin UI exists. No brokerage endpoints are implemented.

Production scheduling: the singleton Gunicorn process starts the persistent background worker. The worker runs independent recurring news detectors and a priority model queue; it also polls the exchange calendar and enqueues one baseline review job, and retains stage completion. Do not horizontally scale this SQLite deployment or use multiple Gunicorn workers. The OS worker lock rejects a second process. Moving to multiple replicas requires a distributed job lease and shared transactional database.

## Backup

Use the SQLite online backup API while the service is running:

```sh
docker compose exec marketbell python -B -m scripts.maintenance backup --output /data/backup.sqlite3
```

The image includes the maintenance module. Copy the backup off-host using the operator's approved secure backup path; retain a separate audit head checkpoint. Never commit runtime databases or private workspace contents. Test restoring into an isolated volume before relying on backups.

## Rollback

1. Stop the service (`docker compose stop marketbell`) to stop the scheduler and drain access.
2. Preserve the current database and image digest. Restore the last verified SQLite backup into the existing volume as `marketbell.sqlite3`; clear only that stopped database's associated WAL/SHM files using an operator-reviewed procedure. Do not restore while a connection is active.
3. Select the previous pinned image digest and recreate the single service. Migrations currently only add schema version 1, so no destructive down migration is required. Verify audit chain, health, replay and tenant isolation before reopening the proxy.

## External actions still required

An approved host/domain and operating budget; a functioning Docker engine/build verification; dedicated deployment secrets if live features are enabled; source/data licensing confirmation; approval before publishing private source or using personal credentials publicly. No new infrastructure charges have been incurred by this task.


## Azure (2026-10-19)

The supported cloud deployment is Azure Container Apps + PostgreSQL Flexible Server; see
`docs/AZURE_DEPLOYMENT_RUNBOOK.md`, `infra/azure/main.bicep` and `docs/AZURE_DEPLOYMENT_READINESS.md`. The single
container `compose.yaml` (SQLite volume, `MARKETBELL_WORKER=1`) remains for one-host use only; do not run SQLite
on network file shares.
