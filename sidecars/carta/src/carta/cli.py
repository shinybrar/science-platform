import subprocess
import sys


def main() -> int:
    # Thin wrapper: forward all args to uvicorn module
    # Usage: uv run carta-sidecar [uvicorn-args]
    cmd = [sys.executable, "-m", "uvicorn"] + sys.argv[1:]
    # If no app is specified, default to carta.backend:app
    has_target = any(":" in a for a in sys.argv[1:])
    if not has_target:
        cmd.append("carta.backend:app")
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 130
