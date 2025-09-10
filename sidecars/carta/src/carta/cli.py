"""CLI entry point for carta-sidecar."""

import subprocess
import sys


def main() -> int:
    """Main entry point for carta-sidecar CLI.

    Thin wrapper that forwards all args to uvicorn module.
    Usage: uv run carta-sidecar [uvicorn-args]

    Returns:
        Exit code from uvicorn process.
    """
    cmd = [sys.executable, "-m", "uvicorn", *sys.argv[1:]]
    # If no app is specified, default to carta.backend:app
    has_target = any(":" in a for a in sys.argv[1:])
    if not has_target:
        cmd.append("carta.backend:app")
    try:
        return subprocess.call(cmd)  # noqa: S603
    except KeyboardInterrupt:
        return 130
