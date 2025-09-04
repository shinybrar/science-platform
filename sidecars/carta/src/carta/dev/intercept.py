# intercept.py
from __future__ import annotations

import contextlib
import signal
import subprocess
import sys
import time
from string import Template
from typing import TYPE_CHECKING

import typer

from carta import TEMPLATE_PATH

if TYPE_CHECKING:
    from pathlib import Path

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
) -> None:
    """Apply the interceptor, stream echo logs, and clean up on Ctrl-C."""
    # 1) read + render template
    raw = template_path.read_text(encoding="utf-8")
    rendered = Template(raw).substitute(NAMESPACE=namespace, SESSION_ID=session_id)

    # 2) kubectl apply
    try:
        run(["kubectl", "apply", "-f", "-"], input_text=rendered, check=True)
    except subprocess.CalledProcessError:
        raise typer.Exit(1)

    # 3) wait a bit
    time.sleep(wait_seconds)

    # 4) stream logs from echo deployment
    dep_name = get_deploy_name(namespace, fallback=echo_deploy)

    # prepare graceful teardown
    stop = False

    def handle_sigint(sig, frame) -> None:
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
        with contextlib.suppress(Exception):
            log_proc.terminate()

        # 6) teardown manifests
        try:
            run(["kubectl", "delete", "-f", "-"], input_text=rendered, check=False)
        except Exception:
            # best-effort cleanup
            pass


if __name__ == "__main__":
    app()
