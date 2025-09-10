"""CARTA ForwardAuth Backend."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

import structlog
from cachetools import TTLCache
from fastapi import FastAPI, Request, Response
from kubernetes import client, config

if TYPE_CHECKING:
    from collections.abc import Mapping

NAMESPACE = os.environ.get("TARGET_NAMESPACE", "skaha-workload")
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "3600"))  # 1 hour default
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

SESSION_RE = re.compile(r"/session/carta/([a-z0-9]+)", re.IGNORECASE)
SESSION_LABEL_KEY = "canfar-net-sessionID"
USER_LABEL_KEY = "canfar-net-userid"

# DEV_MODE: When true, disables SSL verification for K8s client
DEV_MODE = os.environ.get("DEV_MODE", "false").lower() == "true"

# PROD: When true, force the K8s client to use the in-cluster
# ServiceAccount CA certificate for SSL verification.
# Path: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
PROD = os.environ.get("PROD", "false").lower() == "true"

logging.basicConfig(level=LOG_LEVEL, format="%(message)s")
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.NOTSET),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=False,
)
log = structlog.get_logger("carta-sidecar")

try:
    config.load_incluster_config()
    log.info("K8S Mode", mode="incluster")
except Exception:
    config.load_kube_config()
    log.info("K8S Mode", mode="kubeconfig")

if DEV_MODE:
    log.info("DEV MODE ENABLED")
    log.info("Disabling SSL Verification")
    cfg = client.Configuration.get_default_copy()
    cfg.verify_ssl = False
    client.Configuration.set_default(cfg)
    core = client.CoreV1Api()
elif PROD:
    log.info("PROD MODE ENABLED")
    log.info(
        "Enabling SSL Verification with ServiceAccount CA",
        ca_path="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
    )
    cfg = client.Configuration.get_default_copy()
    cfg.verify_ssl = True
    cfg.ssl_ca_cert = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    client.Configuration.set_default(cfg)
    core = client.CoreV1Api()
else:
    log.info("DEV MODE DISABLED")
    log.info("Enabling SSL Verification")
    core = client.CoreV1Api()

cache: TTLCache[str, str] = TTLCache(maxsize=4096, ttl=CACHE_TTL_SECONDS)

app: FastAPI = FastAPI(title="CARTA ForwardAuth Injector", version="1.0.0")
log.info("Starting CARTA ForwardAuth Sidecar")
log.info("Backend Parameter", namespace=NAMESPACE)
log.info("Backend Parameter", LOG_LEVEL=LOG_LEVEL)
log.info("Backend Parameter", DEV_MODE=DEV_MODE)
log.info("Backend Parameter", PROD=PROD)
log.info("Backend Parameter", SESSION_LABEL_KEY=SESSION_LABEL_KEY)
log.info("Backend Parameter", USER_LABEL_KEY=USER_LABEL_KEY)
log.info("Backend Parameter", REGEX_PATTERN=SESSION_RE.pattern)
log.info("Backend Parameter", CACHE_TTL_SECONDS=CACHE_TTL_SECONDS)
log.info("Backend Parameter", APP_PID=os.getpid())


def extract(headers: Mapping[str, str]) -> str | None:
    """Extract CARTA session ID from headers.

    The session ID is parsed from either the `Referer` header URL path
    or the `X-Forwarded-Uri` header using the configured regex pattern.

    Args:
        headers: Mapping of lower-cased header names to values.

    Returns:
        The extracted session ID if found; otherwise ``None``.
    """
    # headers must be lower-cased keys
    referer = headers.get("referer") or ""
    match = SESSION_RE.search(referer)
    if match:
        log.debug("extract_session_id", source="referer", session=match.group(1))
        return match.group(1)

    xfu = headers.get("x-forwarded-uri") or ""
    match = SESSION_RE.search(xfu)
    if match:
        log.debug(
            "extract_session_id", source="x-forwarded-uri", session=match.group(1)
        )
        return match.group(1)

    return None


def lookup(session_id: str) -> str | None:
    """Resolve a user ID for a given session ID.

    This checks an in-memory TTL cache first, then queries Kubernetes
    Services (primary) and Pods (fallback) in the configured namespace
    for a matching session label, returning the associated user label.

    Args:
        session_id: The CARTA session identifier.

    Returns:
        The resolved user ID if found; otherwise ``None``.
    """
    if session_id in cache:
        log.debug("cache_hit", source="cache", session=session_id)
        return cache[session_id]

    # 1) Search pods for the session label
    label_sel = f"{SESSION_LABEL_KEY}={session_id}"
    pods = core.list_namespaced_pod(namespace=NAMESPACE, label_selector=label_sel)
    if pods.items:
        labels = pods.items[0].metadata.labels or {}
        uid = labels.get(USER_LABEL_KEY)
        if uid:
            cache[session_id] = uid
            log.info(
                "map_session_user",
                source="pod",
                session=session_id,
                userid=uid,
                namespace=NAMESPACE,
            )
            log.debug("userid_found", source="pod", session=session_id, userid=uid)
            return uid
        log.warning("no_userid", session=session_id, namespace=NAMESPACE)
    return None


@app.api_route(
    "/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
)
async def auth_any(request: Request, path: str) -> Response:
    """ForwardAuth entrypoint for all routes.

    Traefik ForwardAuth forwards the original request via headers; this
    endpoint extracts the CARTA session ID and injects an auth header
    upstream when a valid user mapping is available.

    Health and documentation endpoints bypass the auth flow.

    Args:
        request: The FastAPI request object containing headers.
        path: The request path captured by the catch-all route.

    Returns:
        A JSON response with status and, on success, the injected
        `carta-auth-token` header.
    """
    # Bypass auth for health endpoints: respond directly here
    if path == "livez":
        return Response(
            content=json.dumps({"status": "ok"}),
            status_code=200,
            media_type="application/json",
        )
    if path == "readyz":
        try:
            core.get_api_resources()
            return Response(
                content=json.dumps({"status": "ready"}),
                status_code=200,
                media_type="application/json",
            )
        except Exception as exc:
            return Response(
                content=json.dumps({"status": "not_ready", "error": str(exc)}),
                status_code=503,
                media_type="application/json",
            )
    lower: dict[str, str] = {k.lower(): v for k, v in request.headers.items()}
    session_id = extract(lower)
    if not session_id:
        log.warning(
            "missing_session",
            path=path,
            referer=lower.get("referer"),
            x_forwarded_uri=lower.get("x-forwarded-uri"),
        )
        return Response(content="missing session id", status_code=403)

    userid = lookup(session_id)
    if not userid:
        log.warning("userid_not_found", session=session_id)
        return Response(content="userid not found", status_code=403)

    # success
    headers: dict[str, str] = {"carta-auth-token": userid}
    body: dict[str, Any] = {"ok": True, "session": session_id, "userid": userid}
    return Response(
        content=json.dumps(body),
        status_code=200,
        media_type="application/json",
        headers=headers,
    )
