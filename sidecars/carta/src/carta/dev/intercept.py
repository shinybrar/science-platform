"""Development tool for intercepting CARTA sessions with ForwardAuth."""

from __future__ import annotations

import contextlib
import json
import signal
import subprocess
import sys
import time
from pathlib import Path  # noqa: TC003
from string import Template
from typing import Any

import structlog
import typer

from carta import TEMPLATE_PATH

log = structlog.get_logger()


app = typer.Typer(add_completion=False)

MIDDLEWARE_NAME = "carta-forwardauth"
# Fully qualified kubectl resource for Traefik IngressRoute CRD:
IR_RES = "ingressroutes.traefik.io"


def run(
    cmd: list[str],
    *,
    input_text: str | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command with optional input and capture.

    Args:
        cmd: Command and arguments to run.
        input_text: Optional input text to send to stdin.
        capture: Whether to capture stdout/stderr.
        check: Whether to check return code.

    Returns:
        CompletedProcess instance.
    """
    if capture:
        return subprocess.run(  # noqa: S603
            cmd,
            input=input_text.encode("utf-8") if input_text is not None else None,
            capture_output=True,
            text=True,
            check=check,
        )
    return subprocess.run(  # noqa: S603
        cmd,
        input=input_text.encode("utf-8") if input_text is not None else None,
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
        check=check,
    )


def get_deploy_name(namespace: str, fallback: str = "carta-echo") -> str:
    """Get deployment name, falling back to label selector if needed.

    Args:
        namespace: Kubernetes namespace.
        fallback: Default deployment name to try first.

    Returns:
        Deployment name.
    """
    try:
        run(["kubectl", "-n", namespace, "get", "deploy", fallback], check=True)
        return fallback
    except subprocess.CalledProcessError:
        proc = run(
            [
                "kubectl",
                "-n",
                namespace,
                "get",
                "deploy",
                "-l",
                "app=carta-echo",
                "-o",
                "jsonpath={.items[0].metadata.name}",
            ],
            capture=True,
            check=False,
        )
        name = (proc.stdout or "").strip()
        return name or fallback


def get_ir_json(namespace: str, name: str) -> dict[str, Any] | None:
    """Get IngressRoute JSON object.

    Args:
        namespace: Kubernetes namespace.
        name: IngressRoute name.

    Returns:
        IngressRoute JSON object or None if not found.
    """
    proc = run(
        ["kubectl", "-n", namespace, "get", IR_RES, name, "-o", "json"],
        capture=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    return json.loads(proc.stdout)


def find_base_ir_by_session(namespace: str, session_id: str) -> str | None:
    """Find the base IngressRoute by session ID.

    Args:
        namespace (str): Kubernetes namespace.
        session_id (str): CARTA session ID.

    Returns:
        str | None: IngressRoute name or None if not found.
    """
    expected = f"skaha-carta-ingress-{session_id}"
    if get_ir_json(namespace, expected):
        return expected

    proc = run(
        ["kubectl", "-n", namespace, "get", IR_RES, "-o", "json"],
        capture=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    data = json.loads(proc.stdout)
    needle = f"/session/carta/{session_id}"
    for item in data.get("items", []):
        routes = item.get("spec", {}).get("routes", [])
        if not routes:
            continue
        match = routes[0].get("match", "") or ""
        if needle in match:
            return item["metadata"]["name"]
    return None


def ensure_forwardauth_on_base_route(
    namespace: str, session_id: str
) -> tuple[bool, str | None, str | None]:
    """Ensure the base session IngressRoute has carta-forwardauth as the FIRST middleware.

    Args:
        namespace (str): Kubernetes namespace.
        session_id (str): CARTA session ID.

    Returns:
        tuple[bool, str | None, str | None]: Tuple of middlwares
    """
    name = find_base_ir_by_session(namespace, session_id)
    if not name:
        return (False, None, None)

    obj = get_ir_json(namespace, name)
    routes = obj.get("spec", {}).get("routes", [])
    if not routes:
        return (False, None, name)

    current = routes[0].get("middlewares", [])
    prev_json = json.dumps(current) if current else None

    # already first?
    if current and current[0].get("name") == MIDDLEWARE_NAME:
        return (False, prev_json, name)

    # build new list with FA first, dedup others
    new_list = [{"name": MIDDLEWARE_NAME}]
    for mw in current or []:
        if mw.get("name") != MIDDLEWARE_NAME:
            new_list.append(mw)

    patch = []
    if current:
        patch.append(
            {"op": "replace", "path": "/spec/routes/0/middlewares", "value": new_list}
        )
    else:
        patch.append(
            {"op": "add", "path": "/spec/routes/0/middlewares", "value": new_list}
        )

    run(
        [
            "kubectl",
            "-n",
            namespace,
            "patch",
            IR_RES,
            name,
            "--type=json",
            "-p",
            json.dumps(patch),
        ],
        check=True,
    )
    return (True, prev_json, name)


def restore_base_route_middlewares(
    namespace: str, base_ir_name: str | None, prev_json: str | None
) -> None:
    """Restore the base IngressRoute middlewares to their previous state.

    Args:
        namespace (str): Kubernetes namespace.
        base_ir_name (str | None): Base IngressRoute name.
        prev_json (str | None): Previous middlewares JSON.

    Returns:
        None
    """
    if not base_ir_name:
        return
    # If there was no previous middlewares field, remove; else restore value.
    if prev_json is None:
        with contextlib.suppress(Exception):
            run(
                [
                    "kubectl",
                    "-n",
                    namespace,
                    "patch",
                    IR_RES,
                    base_ir_name,
                    "--type=json",
                    "-p",
                    json.dumps(
                        [{"op": "remove", "path": "/spec/routes/0/middlewares"}]
                    ),
                ],
                check=False,
            )
        return

    try:
        value = json.loads(prev_json)
    except Exception:
        value = []

    if value:
        patch = [
            {"op": "replace", "path": "/spec/routes/0/middlewares", "value": value}
        ]
    else:
        patch = [{"op": "remove", "path": "/spec/routes/0/middlewares"}]

    run(
        [
            "kubectl",
            "-n",
            namespace,
            "patch",
            IR_RES,
            base_ir_name,
            "--type=json",
            "-p",
            json.dumps(patch),
        ],
        check=False,
    )


@app.command()
def intercept(
    template_path: Path = typer.Option(
        TEMPLATE_PATH, "--template", "-t", help="Path to interceptor.tmpl.yaml"
    ),
    namespace: str = typer.Option(..., "--namespace", "-n"),
    session_id: str = typer.Option(..., "--session-id", "-s"),
    wait: float = typer.Option(
        5.0, "--wait", "-w", help="Seconds to wait before tailing logs"
    ),
    echo_deploy: str = typer.Option(
        "carta-echo", "--echo-deploy", help="Echo Deployment name"
    ),
) -> None:
    """Intercept a CARTA session with ForwardAuth."""
    # 1) render template
    raw = template_path.read_text(encoding="utf-8")
    rendered = Template(raw).substitute(NAMESPACE=namespace, SESSION_ID=session_id)

    # 2) apply template
    log.info("applying_template", namespace=namespace, session_id=session_id)
    run(
        ["kubectl", "-n", namespace, "apply", "-f", "-"],
        input_text=rendered,
        check=True,
    )

    # 3) ensure ForwardAuth on base IngressRoute
    log.info(
        "ensuring_forwardauth",
        namespace=namespace,
        session_id=session_id,
        middleware_name=MIDDLEWARE_NAME,
        ir_resource=IR_RES,
    )
    changed, prev_mw_json, base_ir_name = ensure_forwardauth_on_base_route(
        namespace, session_id
    )
    if base_ir_name:
        log.info("base_ir_name", base_ir_name=base_ir_name)
        if changed:
            log.info("forwardauth_added", base_ir_name=base_ir_name)
        else:
            log.info("forwardauth_present", base_ir_name=base_ir_name)

    # 4) wait and tail echo logs
    time.sleep(wait)
    dep_name = get_deploy_name(namespace, fallback=echo_deploy)

    log.info("tailing_logs", deploy_name=dep_name, namespace=namespace)
    log_proc = subprocess.Popen(
        [
            "kubectl",
            "-n",
            namespace,
            "logs",
            f"deploy/{dep_name}",
            "-f",
            "--timestamps",
        ],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    # 5) wait for signal
    stop = False

    def handle_sig(sig, frame):
        nonlocal stop
        stop = True

    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, handle_sig)

    try:
        while not stop:
            code = log_proc.poll()
            if code is not None:
                break
            time.sleep(0.2)
    finally:
        with contextlib.suppress(Exception):
            log_proc.terminate()

        # 6) delete mirror resources
        log.info("deleting_mirror_resources", namespace=namespace)
        with contextlib.suppress(Exception):
            run(
                ["kubectl", "-n", namespace, "delete", "-f", "-"],
                input_text=rendered,
                check=False,
            )

        # 7) restore base IngressRoute middlewares
        log.info("restoring_base_route_middlewares", base_ir_name=base_ir_name)
        with contextlib.suppress(Exception):
            restore_base_route_middlewares(namespace, base_ir_name, prev_mw_json)

        log.info("done")


if __name__ == "__main__":
    app()
