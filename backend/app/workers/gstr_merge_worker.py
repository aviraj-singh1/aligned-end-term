"""Run with: python -m backend.app.workers.gstr_merge_worker"""

from __future__ import annotations

import signal
import sys
import threading

from backend.app.db.revision import DatabaseRevisionError
from backend.app.services import object_storage
from backend.app.services.gstr_merge_worker_service import run_worker_loop


def main() -> None:
    stop = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        run_worker_loop(stop)
    except (
        DatabaseRevisionError,
        object_storage.ObjectStorageConfigurationError,
        ValueError,
    ) as exc:
        print(f"GSTR worker startup refused: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
