"""Explicit local startup: python -m backend.dev. Never auto-load env in production."""
import argparse
from pathlib import Path

from backend.app.db.config import load_local_environment


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser(description="Run the local ALIGNED backend")
    parser.add_argument("--reload", action="store_true", help="Opt into source watching; restart manually if the Windows reloader stalls")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    load_local_environment()
    # Default to one process: a stalled Windows reloader can keep serving stale code.
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=args.reload,
                **({"reload_dirs": [str(root / "backend/app")]} if args.reload else {}))
