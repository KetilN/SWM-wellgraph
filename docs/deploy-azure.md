# Deployment on Azure (Norway East)

The MCP server and the web app run as one container app. Everything is in the resource
group `SWM-wellgraph`; all resources are in Norway East (the group's own metadata location
is East US, which cannot be changed after creation and does not affect where data or
compute live).

| Resource | Name | Tier | Purpose |
|---|---|---|---|
| Container registry | `swmwellgraph` | Basic | Holds the server image, graph included |
| Container apps environment | `swm-wellgraph-env` | Consumption, no log workspace | Hosts the app |
| Container app | `wellgraph` | 0.5 vCPU, 1 GiB, one instance | MCP endpoint, web page, JSON API |
| Storage account | `swmwellgraph` | Standard, locally redundant, private | Intended for graph files and model replies; see "Storage" |

Public address: `https://wellgraph.happystone-2f8f8670.norwayeast.azurecontainerapps.io`

- `/` web page, `/api/*` JSON, `/mcp` MCP endpoint (streamable HTTP). Read-only.
- The graph is baked into the image, so the app has no runtime dependency on storage.

## Redeploy after the graph or the code changes

```bash
STAGE=$(mktemp -d)
git archive HEAD | tar -x -C "$STAGE"
mkdir "$STAGE/kg" && cp data/kg/*.parquet "$STAGE/kg/"
rm -f "$STAGE/.dockerignore"            # it excludes *.parquet
az acr build -r swmwellgraph -g SWM-wellgraph -t wellgraph-mcp:v2 --platform linux/amd64 "$STAGE"
az containerapp update -n wellgraph -g SWM-wellgraph --image swmwellgraph.azurecr.io/wellgraph-mcp:v2
```

Use a new tag each time; the app only restarts when the image reference changes.

## Storage

The subscription forbids key-based access to storage accounts, and the deploying account
has the Contributor role, which does not include reading or writing blobs. Uploading
needs the "Storage Blob Data Contributor" role on the storage account, granted by a
subscription owner. Until then the account exists but is empty.

## Remove everything

```bash
az containerapp delete -n wellgraph -g SWM-wellgraph --yes
az containerapp env delete -n swm-wellgraph-env -g SWM-wellgraph --yes
az acr delete -n swmwellgraph -g SWM-wellgraph --yes
az storage account delete -n swmwellgraph -g SWM-wellgraph --yes
```

## First-time setup (already done)

```bash
az provider register -n Microsoft.ContainerRegistry
az provider register -n Microsoft.App
az storage account create -n swmwellgraph -g SWM-wellgraph -l norwayeast --sku Standard_LRS \
  --kind StorageV2 --min-tls-version TLS1_2 --allow-blob-public-access false
az acr create -n swmwellgraph -g SWM-wellgraph -l norwayeast --sku Basic --admin-enabled true
az containerapp env create -n swm-wellgraph-env -g SWM-wellgraph -l norwayeast --logs-destination none
az containerapp create -n wellgraph -g SWM-wellgraph --environment swm-wellgraph-env \
  --image swmwellgraph.azurecr.io/wellgraph-mcp:v1 --registry-server swmwellgraph.azurecr.io \
  --registry-username <from az acr credential show> --registry-password <same> \
  --target-port 7860 --ingress external --min-replicas 1 --max-replicas 1 --cpu 0.5 --memory 1.0Gi
```
