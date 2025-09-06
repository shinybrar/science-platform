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
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from typing import Optional

app = typer.Typer(add_completion=False)

MIDDLEWARE_NAME = "carta-forwardauth"


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


def get_ingressroute_json(namespace: str, name: str) -> Optional[dict]:
    proc = run(
        ["kubectl", "-n", namespace, "get", "ingressroute", name, "-o", "json"],
        capture=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    return json.loads(proc.stdout)


def ensure_forwardauth_on_base_route(
    namespace: str, session_id: str
) -> tuple[bool, Optional[str]]:
    """Ensure the base session IngressRoute (skaha-carta-ingress-<id>) has carta-forwardauth as the FIRST middleware.
    Returns (changed, previous_middlewares_json_string or None)
    """
    name = f"skaha-carta-ingress-{session_id}"
    obj = get_ingressroute_json(namespace, name)
    if not obj:
        # Some generators might use different names; try a describe-based fallback?
        # For dev simplicity, just return no-op.
        return (False, None)

    routes = obj.get("spec", {}).get("routes", [])
    if not routes:
        return (False, None)

    # We only touch the first route of this IngressRoute (it’s your session path)
    current = routes[0].get("middlewares", [])
    prev = json.dumps(current) if current else None

    # Is FA already present as the first item?
    already = any(mw.get("name") == MIDDLEWARE_NAME for mw in current)
    if already and current and current[0].get("name") == MIDDLEWARE_NAME:
        return (False, prev)

    # Build new array with FA first (de-duplicate)
    new_list = [{"name": MIDDLEWARE_NAME}]
    for mw in current:
        if mw.get("name") != MIDDLEWARE_NAME:
            new_list.append(mw)

    # If there was no middlewares list, we need an add; else replace
    if current:
        patch = [
            {
                "op": "replace",
                "path": "/spec/routes/0/middlewares",
                "value": new_list,
            }
        ]
    else:
        patch = [
            {
                "op": "add",
                "path": "/spec/routes/0/middlewares",
                "value": new_list,
            }
        ]

    run(
        [
            "kubectl",
            "-n",
            namespace,
            "patch",
            "ingressroute",
            name,
            "--type=json",
            "-p",
            json.dumps(patch),
        ],
        check=True,
    )
    return (True, prev)


def restore_base_route_middlewares(
    namespace: str, session_id: str, prev_json: Optional[str]
) -> None:
    name = f"skaha-carta-ingress-{session_id}"
    # If it never existed, nothing to do
    if prev_json is None:
        # remove the field entirely if we added it
        # (best-effort; ignore failures)
        with contextlib.suppress(Exception):
            run(
                [
                    "kubectl",
                    "-n",
                    namespace,
                    "patch",
                    "ingressroute",
                    name,
                    "--type=json",
                    "-p",
                    json.dumps(
                        [{"op": "remove", "path": "/spec/routes/0/middlewares"}]
                    ),
                ],
                check=False,
            )
        return

    # Otherwise restore
    try:
        value = json.loads(prev_json)
    except Exception:
        value = []

    op = "replace" if value else "remove"
    patch = [{"op": op, "path": "/spec/routes/0/middlewares"}]
    if value:
        patch[0]["value"] = value
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "patch",
            "ingressroute",
            name,
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
    """Apply mirror resources, patch base IngressRoute to require ForwardAuth, tail echo logs,
    and rollback everything on Ctrl-C.
    """
    # 1) render template
    raw = template_path.read_text(encoding="utf-8")
    rendered = Template(raw).substitute(NAMESPACE=namespace, SESSION_ID=session_id)

    # 2) apply template
    print(f"→ Applying mirror to ns={namespace}, session={session_id} ...", flush=True)
    run(["kubectl", "apply", "-f", "-"], input_text=rendered, check=True)

    # 3) patch base IngressRoute to prepend carta-forwardauth (idempotent)
    print(
        f"→ Ensuring ForwardAuth on base IngressRoute skaha-carta-ingress-{session_id} ...",
        flush=True,
    )
    changed, prev_middlewares = ensure_forwardauth_on_base_route(namespace, session_id)
    if changed:
        print("   applied: carta-forwardauth is now first middleware", flush=True)
    else:
        print("   no change needed (already present or route not found)", flush=True)

    # 4) wait, then tail echo logs
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

        # 6) teardown: remove mirror resources
        print("\n→ Deleting mirror resources ...", flush=True)
        with contextlib.suppress(Exception):
            run(["kubectl", "delete", "-f", "-"], input_text=rendered, check=False)

        # 7) restore base IngressRoute middlewares
        print("→ Restoring base IngressRoute middlewares ...", flush=True)
        with contextlib.suppress(Exception):
            restore_base_route_middlewares(namespace, session_id, prev_middlewares)

        print("✓ Done.", flush=True)


if __name__ == "__main__":
    app()
