# CARTA Auth Sidecar

A small FastAPI service used as a ForwardAuth endpoint. It extracts a CARTA session ID from incoming request headers, looks up a Kubernetes Service/Pod with the same `canfar-net-sessionID` label in a target namespace, retrieves the associated `canfar-net-userid`, and returns it in the `carta-auth-token` header when authorized.

## Requirements
- Python 3.10+
- `uv` (recommended) — ultra-fast Python package manager. Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Access to a Kubernetes cluster context, or run in-cluster.

## Configuration
- `TARGET_NAMESPACE`: Kubernetes namespace to search for labeled Services/Pods. Default: `skaha-workload`.
- Kubernetes auth:
  - In-cluster: uses service account via `load_incluster_config()`.
  - Local: falls back to `~/.kube/config` via `load_kube_config()`.

## Install deps with uv
From this directory:

```
uv sync
```

This resolves and installs the dependencies specified in `pyproject.toml` into a `.venv`.

## Run locally
Run with uv’s venv activated automatically:

```
uv run uvicorn app:app --host 0.0.0.0 --port 8000
```

Notes:
- Ensure your kubeconfig points to a cluster with the relevant Services/Pods and labels, or set `TARGET_NAMESPACE` accordingly.
- Any path and method is accepted by the single catch‑all route.

## Example request flow
- Incoming request carries either `Referer` or `X-Forwarded-Uri` containing `/session/carta/<sessionid>`.
- Service looks up Kubernetes objects labeled `canfar-net-sessionID=<sessionid>` in `TARGET_NAMESPACE`.
- If an object has label `canfar-net-userid=<userid>`, the service returns 200 with header `carta-auth-token: <userid>`.
- Otherwise returns 403.

## Docker
Build the container:

```
docker build -t carta-auth-sidecar:local .
```

Run the container:

```
docker run --rm -p 8000:8000 \
  -e TARGET_NAMESPACE=skaha-workload \
  -v $HOME/.kube:/root/.kube:ro \
  carta-auth-sidecar:local
```

- For in-cluster deployment, omit the kubeconfig volume. The pod’s service account must have permissions to list services and pods in `TARGET_NAMESPACE`.

## Kubernetes RBAC (example)
Grant minimal read access within the target namespace:

```
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: carta-auth-read
  namespace: skaha-workload
rules:
- apiGroups: [""]
  resources: ["services", "pods"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: carta-auth-read
  namespace: skaha-workload
subjects:
- kind: ServiceAccount
  name: default # or a dedicated SA
  namespace: skaha-workload
roleRef:
  kind: Role
  name: carta-auth-read
  apiGroup: rbac.authorization.k8s.io
```

## Health check
Endpoints:

```
curl -i http://localhost:8000/livez   # liveness
curl -i http://localhost:8000/readyz  # readiness (checks K8s API reachability)
```

The catch‑all route will respond 403 unless a valid session can be derived from headers.

## Docker Compose (local)
Build and run with your local kubeconfig mounted:

```
docker compose up --build
```

Then visit `http://localhost:8000/livez`.

## Kubernetes Manifests
Basic manifests are provided under `k8s/` with probes wired to `/livez` and `/readyz`.

Apply to a cluster/namespace of your choice:

```
kubectl apply -f k8s/
```

Notes:
- Update the image reference in `k8s/deployment.yaml` to a registry you can pull from (e.g., `ghcr.io/<org>/carta-auth-sidecar:<tag>`).
- Ensure RBAC is configured as described above so the service account can list services/pods in `TARGET_NAMESPACE`.

## Development
- Format/typing tools are not enforced here; feel free to add `ruff`/`mypy` locally.
- The service is intentionally lean: one file `app.py`, no DB, 10‑minute TTL cache.
