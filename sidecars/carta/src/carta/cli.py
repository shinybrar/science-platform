import sys
import subprocess


def main() -> int:
    # Thin wrapper: forward all args to uvicorn module
    # Usage: uv run carta-sidecar [uvicorn-args]
    cmd = [sys.executable, "-m", "uvicorn"] + sys.argv[1:]
    # If no app is specified, default to carta.app:app
    if not any(":" in a or a.startswith("carta.app:app") for a in sys.argv[1:]):
        cmd.append("carta.app:app")
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 130

