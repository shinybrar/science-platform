# intercept.py
from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path
from string import Template

import typer

from carta import TEMPLATE_PATH

app = typer.Typer(add_completion=False)


def run(
    cmd: list[str], *, input_text: str | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        input=input_text.encode("utf-8") if input_text is not None else None,
        stdout=sys.stdout,
        stderr=sys.stderr,
        check=check,
    )


def get_deploy_name(namespace: str, fallback: str = "carta-echo") -> str:
    # Prefer explicit name; if not found, try label selector
    try:
        run(["kubectl", "-n", namespace, "get", "deploy", fallback], check=True)
        return fallback
    except subprocess.CalledProcessError:
        # discover by label app=carta-echo
        proc = subprocess.run(
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
            capture_output=True,
            text=True,
            check=False,
        )
        name = (proc.stdout or "").strip()
        if name:
            return name
        # last resort, still try fallback
        return fallback


@app.command()
def intercept(
    template_path: Path = typer.Option(
        TEMPLATE_PATH, "--template", "-t", help="Path to interceptor.tmpl.yaml"
    ),
    namespace: str = typer.Option(..., "--namespace", "-n"),
    session_id: str = typer.Option(..., "--session-id", "-s"),
    wait_seconds: float = typer.Option(
        5.0, "--wait", "-w", help="Seconds to wait before tailing logs"
    ),
    echo_deploy: str = typer.Option(
        "carta-echo", "--echo-deploy", help="Echo Deployment name (default: carta-echo)"
    ),
):
    """
    Apply the interceptor, stream echo logs, and clean up on Ctrl-C.
    """
    # 1) read + render template
    raw = template_path.read_text(encoding="utf-8")
    rendered = Template(raw).substitute(NAMESPACE=namespace, SESSION_ID=session_id)

    # 2) kubectl apply
    print(
        f"→ Applying interceptor into ns={namespace}, session={session_id} ...",
        flush=True,
    )
    try:
        run(["kubectl", "apply", "-f", "-"], input_text=rendered, check=True)
    except subprocess.CalledProcessError:
        print("✗ kubectl apply failed.", file=sys.stderr)
        raise typer.Exit(1)

    # 3) wait a bit
    print(
        f"→ Waiting {wait_seconds:.0f}s for resources to become ready ...", flush=True
    )
    time.sleep(wait_seconds)

    # 4) stream logs from echo deployment
    dep_name = get_deploy_name(namespace, fallback=echo_deploy)
    print(
        f"→ Tailing logs from Deployment/{dep_name} in ns={namespace} (Ctrl-C to stop) ...",
        flush=True,
    )

    # prepare graceful teardown
    stop = False

    def handle_sigint(sig, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    # use Popen so we can interrupt and then cleanup
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
    try:
        while not stop:
            # if logs exit on their own, break
            code = log_proc.poll()
            if code is not None:
                break
            time.sleep(0.2)
    finally:
        print("\n→ Stopping log stream ...", flush=True)
        try:
            log_proc.terminate()
        except Exception:
            pass

        # 6) teardown manifests
        print("→ Deleting interceptor resources ...", flush=True)
        try:
            run(["kubectl", "delete", "-f", "-"], input_text=rendered, check=False)
        except Exception:
            # best-effort cleanup
            pass

        print("✓ Done.", flush=True)


if __name__ == "__main__":
    app()
