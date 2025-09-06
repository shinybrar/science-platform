# intercept.py
from __future__ import annotations

import contextlib
import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from string import Template
from typing import Optional

import typer

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
) -> subprocess.CompletedProcess:
    if capture:
        return subprocess.run(
            cmd,
            input=input_text.encode("utf-8") if input_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=check,
        )
    return subprocess.run(
        cmd,
        input=input_text.encode("utf-8") if input_text is not None else None,
        stdout=sys.stdout,
        stderr=sys.stderr,
        check=check,
    )


def get_deploy_name(namespace: str, fallback: str = "carta-echo") -> str:
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


def get_ir_json(namespace: str, name: str) -> Optional[dict]:
    proc = run(
        ["kubectl", "-n", namespace, "get", IR_RES, name, "-o", "json"],
        capture=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    return json.loads(proc.stdout)


def find_base_ir_by_session(namespace: str, session_id: str) -> Optional[str]:
    """If skaha-carta-ingress-<session> doesn't exist, scan for any IngressRoute
    whose first route's match contains the session PathPrefix.
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
) -> tuple[bool, Optional[str], Optional[str]]:
    """Ensure the base session IngressRoute has carta-forwardauth as the FIRST middleware.
    Returns (changed, previous_middlewares_json, base_ir_name)
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
    namespace: str, base_ir_name: Optional[str], prev_json: Optional[str]
) -> None:
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
        ..., "--template", "-t", help="Path to interceptor.tmpl.yaml"
    ),
    namespace: str = typer.Option(..., "--namespace", "-n"),
    session_id: str = typer.Option(..., "--session-id", "-s"),
    wait_seconds: float = typer.Option(
        5.0, "--wait", "-w", help="Seconds to wait before tailing logs"
    ),
    echo_deploy: str = typer.Option(
        "carta-echo", "--echo-deploy", help="Echo Deployment name"
    ),
):
    """Apply mirror resources, patch base IngressRoute to require ForwardAuth, tail echo logs, then clean up."""
    # 1) render template
    raw = template_path.read_text(encoding="utf-8")
    rendered = Template(raw).substitute(NAMESPACE=namespace, SESSION_ID=session_id)

    # 2) apply template
    print(f"→ Applying mirror to ns={namespace}, session={session_id} ...", flush=True)
    run(
        ["kubectl", "-n", namespace, "apply", "-f", "-"],
        input_text=rendered,
        check=True,
    )

    # 3) ensure ForwardAuth on base IngressRoute
    print(
        f"→ Ensuring ForwardAuth on base {IR_RES}/skaha-carta-ingress-{session_id} (or equivalent) ...",
        flush=True,
    )
    changed, prev_mw_json, base_ir_name = ensure_forwardauth_on_base_route(
        namespace, session_id
    )
    if base_ir_name:
        print(f"   base route: {base_ir_name}", flush=True)
    print(
        (
            "   applied: carta-forwardauth is now first middleware"
            if changed
            else "   no change needed (already present or base route not found)"
        ),
        flush=True,
    )

    # 4) wait and tail echo logs
    time.sleep(wait_seconds)
    dep_name = get_deploy_name(namespace, fallback=echo_deploy)
    print(
        f"→ Tailing logs: deploy/{dep_name} in {namespace} (Ctrl-C to stop)", flush=True
    )
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
        print("\n→ Deleting mirror resources ...", flush=True)
        with contextlib.suppress(Exception):
            run(
                ["kubectl", "-n", namespace, "delete", "-f", "-"],
                input_text=rendered,
                check=False,
            )

        # 7) restore base IngressRoute middlewares
        print("→ Restoring base IngressRoute middlewares ...", flush=True)
        with contextlib.suppress(Exception):
            restore_base_route_middlewares(namespace, base_ir_name, prev_mw_json)

        print("✓ Done.", flush=True)


if __name__ == "__main__":
    app()
