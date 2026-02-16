"""Automation framework for scheduled tasks, webhooks, and browser control"""

from .scheduler import CronScheduler, ScheduledTask
from .webhooks import WebhookServer, Webhook

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
    "BrowserAutomation", "BrowserResult", "get_browser", "close_browser", "PLAYWRIGHT_AVAILABLE"
]
