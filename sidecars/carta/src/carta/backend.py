import json
import logging
import os
import re
from typing import Optional

import structlog
from cachetools import TTLCache
from fastapi import FastAPI, Request, Response
from kubernetes import client, config

APP_NAMESPACE = os.environ.get("TARGET_NAMESPACE", "skaha-workload")
SESSION_RE = re.compile(r"/session/carta/([a-z0-9]+)", re.IGNORECASE)

# 600s TTL cache for session -> userid
cache = TTLCache(maxsize=2048, ttl=600)

# In-cluster k8s client
try:
    config.load_incluster_config()
except Exception:
    # fallback for local testing
    config.load_kube_config()
core = client.CoreV1Api()

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
_level = getattr(logging, LOG_LEVEL, logging.INFO)

# Configure stdlib logging so uvicorn/fastapi integrate with our level
logging.basicConfig(level=_level)

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.EventRenamer("message"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(_level),
    context_class=dict,
)

logger = structlog.get_logger("carta.sidecar")

app = FastAPI()


def extract_session_id(headers) -> Optional[str]:
    ref = headers.get("referer") or ""
    m = SESSION_RE.search(ref)
    if m:
        return m.group(1)
    # Fallback to X-Forwarded-Uri if Referer absent
    xfu = headers.get("x-forwarded-uri") or ""
    m = SESSION_RE.search(xfu)
    if m:
        return m.group(1)
    return None


def lookup_userid(session_id: str) -> Optional[str]:
    # cache hit?
    if session_id in cache:
        return cache[session_id]
    # 1) try Service labeled with the session
    label_sel = f"canfar-net-sessionID={session_id}"
    svcs = core.list_namespaced_service(
        namespace=APP_NAMESPACE, label_selector=label_sel
    )
    if svcs.items:
        labels = svcs.items[0].metadata.labels or {}
        uid = labels.get("canfar-net-userid")
        if uid:
            cache[session_id] = uid
            return uid
    # 2) fallback: look at Pods with the same label
    pods = core.list_namespaced_pod(namespace=APP_NAMESPACE, label_selector=label_sel)
    if pods.items:
        labels = pods.items[0].metadata.labels or {}
        uid = labels.get("canfar-net-userid")
        if uid:
            cache[session_id] = uid
            return uid
    return None


@app.get("/livez")
async def livez():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    try:
        core.get_api_resources()
        return {"status": "ready"}
    except Exception as exc:
        return Response(
            content=json.dumps({"status": "not_ready", "error": str(exc)}),
            status_code=503,
            media_type="application/json",
        )


@app.api_route(
    "/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
)
async def auth_any(request: Request, path: str):
    # extract session id
    lowered = {k.lower(): v for k, v in request.headers.items()}
    session_id = extract_session_id(lowered)
    if not session_id:
        # deny if we can't identify session
        header_used = (
            "referer"
            if SESSION_RE.search(lowered.get("referer", ""))
            else (
                "x-forwarded-uri"
                if SESSION_RE.search(lowered.get("x-forwarded-uri", ""))
                else "<none>"
            )
        )
        logger.debug(
            "auth",
            req=request.method,
            path=request.url.path,
            header_used=header_used,
            session_id=None,
            userid=None,
            result="missing_session",
        )
        return Response(content="missing session id", status_code=403)

    userid = lookup_userid(session_id)
    if not userid:
        header_used = (
            "referer"
            if SESSION_RE.search(lowered.get("referer", ""))
            else "x-forwarded-uri"
        )
        logger.debug(
            "auth",
            req=request.method,
            path=request.url.path,
            header_used=header_used,
            session_id=session_id,
            userid=None,
            result="userid_not_found",
        )
        return Response(content="userid not found", status_code=403)

    # forwardAuth success: return 200 and include carta-auth-token
    headers = {"carta-auth-token": userid}
    body = json.dumps({"ok": True, "session": session_id, "userid": userid})
    header_used = (
        "referer"
        if SESSION_RE.search(lowered.get("referer", ""))
        else "x-forwarded-uri"
    )
    logger.debug(
        "auth",
        req=request.method,
        path=request.url.path,
        header_used=header_used,
        session_id=session_id,
        userid=userid,
        result="ok",
    )
    return Response(
        content=body, status_code=200, media_type="application/json", headers=headers
    )
