# app.py
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional, Dict

from cachetools import TTLCache
from fastapi import FastAPI, Request, Response
from kubernetes import client, config

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
APP_NAMESPACE = os.environ.get("TARGET_NAMESPACE", "skaha-workload")
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "3600"))  # 1 hour default
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

SESSION_RE = re.compile(r"/session/carta/([a-z0-9]+)", re.I)
SESSION_LABEL_KEY = os.environ.get("SESSION_LABEL_KEY", "canfar-net-sessionID")
USER_LABEL_KEY = os.environ.get("USER_LABEL_KEY", "canfar-net-userid")

# -----------------------------------------------------------------------------
# Logging (structured-ish)
# -----------------------------------------------------------------------------
logging.basicConfig(level=LOG_LEVEL, format="%(message)s")
log = logging.getLogger("carta-sidecar")

def jlog(level: int, msg: str, **fields):
    payload = {"msg": msg, "level": logging.getLevelName(level), **fields}
    log.log(level, json.dumps(payload))

# -----------------------------------------------------------------------------
# K8s client
# -----------------------------------------------------------------------------
try:
    config.load_incluster_config()
    jlog(logging.INFO, "k8s_auth", mode="incluster")
except Exception:
    config.load_kube_config()
    jlog(logging.INFO, "k8s_auth", mode="kubeconfig")

core = client.CoreV1Api()

# -----------------------------------------------------------------------------
# Cache
# -----------------------------------------------------------------------------
cache: TTLCache[str, str] = TTLCache(maxsize=4096, ttl=CACHE_TTL_SECONDS)

# -----------------------------------------------------------------------------
# App
# -----------------------------------------------------------------------------
app = FastAPI(title="CARTA ForwardAuth Injector", version="1.0.0")

def extract_session_id(headers: Dict[str, str]) -> Optional[str]:
    # headers must be lower-cased keys
    ref = headers.get("referer") or ""
    m = SESSION_RE.search(ref)
    if m:
        return m.group(1)

    xfu = headers.get("x-forwarded-uri") or ""
    m = SESSION_RE.search(xfu)
    if m:
        return m.group(1)

    return None

def lookup_userid(session_id: str) -> Optional[str]:
    if session_id in cache:
        jlog(logging.DEBUG, "cache_hit", session=session_id)
        return cache[session_id]

    # 1) Services
    label_sel = f"{SESSION_LABEL_KEY}={session_id}"
    svcs = core.list_namespaced_service(namespace=APP_NAMESPACE, label_selector=label_sel)
    if svcs.items:
        labels = svcs.items[0].metadata.labels or {}
        uid = labels.get(USER_LABEL_KEY)
        if uid:
            cache[session_id] = uid
            jlog(logging.INFO, "map_session_user", source="service", session=session_id, userid=uid, namespace=APP_NAMESPACE)
            return uid

    # 2) Pods (fallback)
    pods = core.list_namespaced_pod(namespace=APP_NAMESPACE, label_selector=label_sel)
    if pods.items:
        labels = pods.items[0].metadata.labels or {}
        uid = labels.get(USER_LABEL_KEY)
        if uid:
            cache[session_id] = uid
            jlog(logging.INFO, "map_session_user", source="pod", session=session_id, userid=uid, namespace=APP_NAMESPACE)
            return uid

    return None

@app.api_route("/{path:path}", methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS","HEAD"])
async def auth_any(request: Request, path: str):
    # Traefik ForwardAuth passes original request context via headers
    # We only need to return 200 + a response header to be injected upstream.
    lower = {k.lower(): v for k, v in request.headers.items()}
    session_id = extract_session_id(lower)
    if not session_id:
        jlog(logging.WARNING, "missing_session", referer=lower.get("referer"), x_forwarded_uri=lower.get("x-forwarded-uri"))
        return Response(content="missing session id", status_code=403)

    userid = lookup_userid(session_id)
    if not userid:
        jlog(logging.WARNING, "userid_not_found", session=session_id)
        return Response(content="userid not found", status_code=403)

    # success
    headers = {"carta-auth-token": userid}
    body = {"ok": True, "session": session_id, "userid": userid}
    return Response(content=json.dumps(body), status_code=200, media_type="application/json", headers=headers)

@app.get("/livez")
async def livez():
    return {"status": "ok"}

@app.get("/readyz")
async def readyz():
    try:
        # Lightweight call - doesn't fetch heavy data
        core.get_api_resources()
        return {"status": "ready"}
    except Exception as exc:
        return Response(
            content=json.dumps({"status": "not_ready", "error": str(exc)}),
            status_code=503,
            media_type="application/json",
        )
