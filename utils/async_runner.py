"""Run async coroutines from sync context, keeping GUI responsive when available."""

import asyncio
import threading
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine from a sync context (e.g. GUI button handler).

    When QApplication is available, runs the coro in a background thread and
    processes Qt events while waiting, keeping the GUI responsive.
    Otherwise falls back to asyncio.run().

    Raises:
        The same exception the coroutine would raise.
    """
    result = [None]
    exception = [None]

    def task():
        try:
            result[0] = asyncio.run(coro)
        except Exception as e:
            exception[0] = e

    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app:
            thread = threading.Thread(target=task, daemon=True)
            thread.start()
            while thread.is_alive() and app:
                app.processEvents()
            thread.join(timeout=0.1)
        else:
            result[0] = asyncio.run(coro)
    except ImportError:
        result[0] = asyncio.run(coro)
    except Exception:
        thread = threading.Thread(target=task, daemon=True)
        thread.start()
        thread.join()

    if exception[0]:
        raise exception[0]
    return result[0]
