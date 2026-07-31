"""Convenience entrypoint: `python run.py`.

Re-executes itself with the project virtualenv when the interpreter that
launched it can't see the dependencies. Both the system Python and the venv
expose a `uvicorn` script on this machine, so a bare `uvicorn app.main:app` can
silently pick the wrong one and fail with `ModuleNotFoundError: No module named
'fastapi'`. Going through here (or `./start.sh`) cannot hit that.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"


def _ensure_venv() -> None:
    try:
        import fastapi  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    if not VENV_PYTHON.exists():
        sys.exit(
            f"Dependencies are missing and there is no virtualenv at {VENV_PYTHON}.\n"
            "Create one with:\n"
            "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
        )
    # Loop guard. It has to be an env marker, not a path comparison:
    # .venv/bin/python is a symlink to the system interpreter, so resolving the
    # two paths makes them compare equal and the re-exec would never happen.
    if os.environ.get("RA_BACKEND_REEXEC") == "1":
        sys.exit(
            f"{VENV_PYTHON} is missing dependencies. Install them with:\n"
            "  .venv/bin/pip install -r requirements.txt"
        )
    print(f"[run] switching to the project virtualenv: {VENV_PYTHON}")
    os.environ["RA_BACKEND_REEXEC"] = "1"
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])


_ensure_venv()

import uvicorn  # noqa: E402

from app.config import settings  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
