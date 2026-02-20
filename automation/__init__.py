"""Automation framework for scheduled tasks, webhooks, browser control, and file/Git triggers"""

from .scheduler import CronScheduler, ScheduledTask
from .webhooks import WebhookServer, Webhook
from .triggers import (
    FILE_CHANGE_EVENT,
    GIT_EVENT,
    get_matching_triggers,
    execute_trigger_actions,
    load_triggers,
)
from .file_watcher import FileWatcher, load_watch_dirs

# Browser automation (optional - requires playwright)
try:
    from .browser import BrowserAutomation, BrowserResult, get_browser, close_browser, PLAYWRIGHT_AVAILABLE
except ImportError:
    BrowserAutomation = None
    BrowserResult = None
    get_browser = None
    close_browser = None
    PLAYWRIGHT_AVAILABLE = False

__all__ = [
    "CronScheduler", "ScheduledTask",
    "WebhookServer", "Webhook",
    "BrowserAutomation", "BrowserResult", "get_browser", "close_browser", "PLAYWRIGHT_AVAILABLE",
    "FILE_CHANGE_EVENT", "GIT_EVENT", "get_matching_triggers", "execute_trigger_actions", "load_triggers",
    "FileWatcher", "load_watch_dirs",
]
