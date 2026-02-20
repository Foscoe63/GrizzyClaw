"""File and Git event watcher for triggers (file_change, git_event)."""

import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

WATCHER_CONFIG_PATH = Path.home() / ".grizzyclaw" / "file_watcher.json"


def load_watch_dirs() -> List[Path]:
    """Load watch directories from config or return default."""
    if WATCHER_CONFIG_PATH.exists():
        try:
            with open(WATCHER_CONFIG_PATH, "r") as f:
                data = json.load(f)
            dirs = data.get("watch_dirs") or []
            return [Path(d).expanduser().resolve() for d in dirs if d]
        except Exception as e:
            logger.warning("Could not load file_watcher config: %s", e)
    default = Path.home() / "projects"
    if default.exists():
        return [default]
    return []


def _is_git_event(src_path: str) -> bool:
    return ".git" in Path(src_path).parts


def _git_repo_root(path: Path) -> Optional[Path]:
    """Return repo root containing .git or None."""
    p = path.resolve()
    for _ in range(20):
        if (p / ".git").exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return None


class FileWatcher:
    """Watch directories for file changes and Git events; invoke async callback with context."""

    def __init__(
        self,
        loop: Any,
        on_event: Callable[[Dict[str, Any]], Awaitable[None]],
        watch_dirs: Optional[List[Path]] = None,
    ):
        self.loop = loop
        self.on_event = on_event
        self.watch_dirs = watch_dirs or load_watch_dirs()
        self._observer = None
        self._thread = None

    def start(self) -> bool:
        if not self.watch_dirs:
            logger.debug("No watch dirs configured; file watcher not started")
            return False
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent
        except ImportError:
            logger.warning("watchdog not installed; file watcher disabled")
            return False

        class Handler(FileSystemEventHandler):
            def __init__(self, loop, on_event_coro):
                self.loop = loop
                self.on_event_coro = on_event_coro

            def _emit(self, event_type: str, src_path: str, is_directory: bool):
                if is_directory:
                    return
                path = Path(src_path).resolve()
                if _is_git_event(src_path):
                    event = "git_event"
                    repo = _git_repo_root(path)
                    ctx = {
                        "event": event,
                        "path": str(path),
                        "file_path": str(path),
                        "change_type": event_type,
                        "repo": str(repo) if repo else "",
                    }
                else:
                    event = "file_change"
                    ctx = {
                        "event": event,
                        "path": str(path),
                        "file_path": str(path),
                        "change_type": event_type,
                    }
                try:
                    import asyncio
                    asyncio.run_coroutine_threadsafe(self.on_event_coro(ctx), self.loop)
                except Exception as e:
                    logger.debug("File watcher callback error: %s", e)

            def on_modified(self, event):
                if event.is_directory:
                    return
                self._emit("modified", getattr(event, "src_path", ""), event.is_directory)

            def on_created(self, event):
                if event.is_directory:
                    return
                self._emit("created", getattr(event, "src_path", ""), event.is_directory)

        self._observer = Observer()
        handler = Handler(self.loop, self.on_event)
        for d in self.watch_dirs:
            if not d.exists():
                logger.warning("Watch dir does not exist: %s", d)
                continue
            self._observer.schedule(handler, str(d), recursive=True)
        self._observer.start()
        logger.info("File watcher started for %s", [str(d) for d in self.watch_dirs])
        return True

    def stop(self) -> None:
        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception as e:
                logger.debug("File watcher stop: %s", e)
            self._observer = None
