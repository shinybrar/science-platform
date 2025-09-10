"""CARTA authentication sidecar package."""

from pathlib import Path

BASE_PATH: Path = Path(__file__).parent
TEMPLATE_PATH: Path = BASE_PATH / "dev" / "interceptor.tmpl.yaml"

__all__ = ["BASE_PATH", "TEMPLATE_PATH"]
