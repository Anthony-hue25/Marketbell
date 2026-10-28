#!/usr/bin/env bash
# Manual, approval-gated deployment helper (the CI pipeline does the same with OIDC).
#   ./infra/azure/deploy.sh preview  staging  <resource-group>                 # what-if only; changes nothing
#   ./infra/azure/deploy.sh platform staging  <resource-group>                 # phase 1 (no apps)
#   ./infra/azure/deploy.sh apps     staging  <resource-group> <image@sha256>  # phase 2
#   ./infra/azure/deploy.sh rollback staging  <resource-group> <previous image@sha256>
set -euo pipefail
action=${1:?preview|platform|apps|rollback}; env=${2:?staging|production}; rg=${3:?resource group}; image=${4:-}
here=$(cd "$(dirname "$0")" && pwd)
params="$here/$env.bicepparam"
if [[ "$action" == apps || "$action" == rollback ]]; then
  [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || { echo "Refusing: image must be pinned by digest (repo@sha256:...)"; exit 2; }
fi
case "$action" in
  preview)  az deployment group what-if -g "$rg" -f "$here/main.bicep" -p "$params" ${image:+-p deployApps=true image=$image} ;;
  platform) az deployment group create -g "$rg" -f "$here/main.bicep" -p "$params" -p deployApps=false --name "platform-$(date +%Y%m%d%H%M%S)" ;;
  apps|rollback)
            az deployment group what-if -g "$rg" -f "$here/main.bicep" -p "$params" -p deployApps=true image="$image"
            read -r -p "Apply to $env ($rg) with $image ? Type the environment name to confirm: " ok
            [[ "$ok" == "$env" ]] || { echo "Not confirmed; nothing changed."; exit 3; }
            az deployment group create -g "$rg" -f "$here/main.bicep" -p "$params" -p deployApps=true image="$image" --name "$action-$(date +%Y%m%d%H%M%S)" ;;
  *) echo "unknown action"; exit 2 ;;
esac
