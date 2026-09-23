"""Shared settings: loads .env next to the scripts, picks the Jev model."""
import os
from pathlib import Path

WORKDIR = Path(os.environ.get("JEV_WORKDIR") or Path.cwd())
_ENV = WORKDIR / ".env"
if _ENV.exists():
    for line in _ENV.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))

# "~typesafe/jev-latest" follows TypeSafe's newest release. Pin a version
# (e.g. "typesafe/jev-1.13") when you need runs to be comparable over time.
JEV_MODEL = os.environ.get("JEV_MODEL", "~typesafe/jev-latest")
WRITER_MODEL = os.environ.get("WRITER_MODEL", "anthropic/claude-sonnet-5")
