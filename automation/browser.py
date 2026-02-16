"""Browser automation using Playwright"""

import asyncio
import base64
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# System Playwright driver path (found at runtime)
_SYSTEM_PLAYWRIGHT_DRIVER = None

def _find_system_playwright_driver():
    """Find system-installed Playwright driver (node binary)"""
    global _SYSTEM_PLAYWRIGHT_DRIVER
    if _SYSTEM_PLAYWRIGHT_DRIVER:
        return _SYSTEM_PLAYWRIGHT_DRIVER
    
    # Try common Python framework locations on macOS
    for py_ver in ['3.14', '3.13', '3.12', '3.11', '3.10']:
        driver_path = f'/Library/Frameworks/Python.framework/Versions/{py_ver}/lib/python{py_ver}/site-packages/playwright/driver/node'
        if os.path.exists(driver_path):
            _SYSTEM_PLAYWRIGHT_DRIVER = driver_path
            logger.info(f"Found system Playwright driver: {driver_path}")
            return driver_path
    
    # Try homebrew Python locations
    for py_ver in ['3.14', '3.13', '3.12', '3.11', '3.10']:
        homebrew_paths = [
            f'/opt/homebrew/lib/python{py_ver}/site-packages/playwright/driver/node',
            f'/usr/local/lib/python{py_ver}/site-packages/playwright/driver/node',
        ]
        for driver_path in homebrew_paths:
            if os.path.exists(driver_path):
                _SYSTEM_PLAYWRIGHT_DRIVER = driver_path
                logger.info(f"Found system Playwright driver: {driver_path}")
                return driver_path
    
    # Try user site-packages
    import site
    for site_path in site.getsitepackages() + [site.getusersitepackages()]:
        driver_path = os.path.join(site_path, 'playwright', 'driver', 'node')
        if os.path.exists(driver_path):
            _SYSTEM_PLAYWRIGHT_DRIVER = driver_path
            logger.info(f"Found system Playwright driver: {driver_path}")
            return driver_path
    
    logger.warning("Could not find system Playwright driver")
    return None

# When running as frozen app, we need to patch Playwright to use system driver
PLAYWRIGHT_AVAILABLE = False
if getattr(sys, 'frozen', False):
    # Use system-installed browsers
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '0')
    
    # Find system driver BEFORE importing playwright
    system_driver = _find_system_playwright_driver()
    
    if system_driver:
        try:
            # Import playwright's internal module to patch driver path
            import playwright._impl._driver as pw_driver
            
            # Save original function
            _original_compute_driver_executable = pw_driver.compute_driver_executable
            
            # Create patched function that returns system driver
            # Original function returns tuple: (node_binary_path, cli_js_path)
            def _patched_compute_driver_executable():
                driver_dir = Path(system_driver).parent
                cli_js = driver_dir / "package" / "cli.js"
                return (Path(system_driver), cli_js)
            
            # Apply patch
            pw_driver.compute_driver_executable = _patched_compute_driver_executable
            logger.info(f"Patched Playwright to use system driver: {system_driver}")
            
            # Now import the rest of playwright
            from playwright.async_api import async_playwright, Browser, Page, BrowserContext
            PLAYWRIGHT_AVAILABLE = True
        except Exception as e:
            logger.warning(f"Failed to patch Playwright for frozen app: {e}")
            PLAYWRIGHT_AVAILABLE = False
    else:
        logger.warning("Browser automation unavailable: no system Playwright driver found")
else:
    # Normal (non-frozen) import
    try:
        from playwright.async_api import async_playwright, Browser, Page, BrowserContext
        PLAYWRIGHT_AVAILABLE = True
    except ImportError:
        PLAYWRIGHT_AVAILABLE = False
        logger.warning("Playwright not installed. Run: pip install playwright && playwright install chromium")


@dataclass
class BrowserResult:
    """Result of a browser action"""
    success: bool
    action: str
    url: Optional[str] = None
    title: Optional[str] = None
    screenshot_path: Optional[str] = None
    screenshot_base64: Optional[str] = None
    content: Optional[str] = None
    error: Optional[str] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action,
            "url": self.url,
            "title": self.title,
            "screenshot_path": self.screenshot_path,
            "has_screenshot": self.screenshot_base64 is not None,
            "content_length": len(self.content) if self.content else 0,
            "error": self.error,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }


class BrowserAutomation:
    """Browser automation controller using Playwright
    
    Provides capabilities for:
    - Navigating to URLs
    - Taking screenshots
    - Extracting page content/text
    - Filling forms
    - Clicking elements
    - Running JavaScript
    """

    def __init__(self, headless: bool = True, screenshots_dir: str = None):
        """Initialize browser automation
        
        Args:
            headless: Run browser in headless mode (no visible window)
            screenshots_dir: Directory to save screenshots (default: ~/.grizzyclaw/screenshots)
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed. Run: pip install playwright && playwright install chromium")
        
        self.headless = headless
        self.screenshots_dir = Path(screenshots_dir or Path.home() / ".grizzyclaw" / "screenshots")
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._started = False

    async def start(self):
        """Start the browser"""
        if self._started:
            return
        
        logger.info("Starting browser automation...")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=['--disable-blink-features=AutomationControlled']
        )
        self._context = await self._browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        self._page = await self._context.new_page()
        self._started = True
        logger.info("✓ Browser started")

    async def stop(self):
        """Stop the browser"""
        if not self._started:
            return
        
        logger.info("Stopping browser automation...")
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._started = False
        logger.info("✓ Browser stopped")

    async def close(self):
        """Close the browser (alias for stop)"""
        await self.stop()

    async def _ensure_started(self):
        """Ensure browser is started"""
        if not self._started:
            await self.start()

    async def navigate(self, url: str, wait_for: str = "load", timeout: int = 30000) -> BrowserResult:
        """Navigate to a URL
        
        Args:
            url: URL to navigate to
            wait_for: Wait condition ('load', 'domcontentloaded', 'networkidle')
            timeout: Timeout in milliseconds
            
        Returns:
            BrowserResult with page info
        """
        await self._ensure_started()
        
        try:
            await self._page.goto(url, wait_until=wait_for, timeout=timeout)
            title = await self._page.title()
            current_url = self._page.url
            
            logger.info(f"Navigated to: {current_url} - {title}")
            return BrowserResult(
                success=True,
                action="navigate",
                url=current_url,
                title=title
            )
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            return BrowserResult(
                success=False,
                action="navigate",
                url=url,
                error=str(e)
            )

    async def screenshot(self, full_page: bool = False, element_selector: str = None) -> BrowserResult:
        """Take a screenshot
        
        Args:
            full_page: Capture full scrollable page
            element_selector: Optional CSS selector to screenshot specific element
            
        Returns:
            BrowserResult with screenshot path and base64
        """
        await self._ensure_started()
        
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screenshot_{timestamp}.png"
            filepath = self.screenshots_dir / filename
            
            if element_selector:
                element = await self._page.query_selector(element_selector)
                if element:
                    screenshot_bytes = await element.screenshot()
                else:
                    return BrowserResult(
                        success=False,
                        action="screenshot",
                        error=f"Element not found: {element_selector}"
                    )
            else:
                screenshot_bytes = await self._page.screenshot(full_page=full_page)
            
            # Save to file
            with open(filepath, 'wb') as f:
                f.write(screenshot_bytes)
            
            # Also encode as base64
            b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
            
            logger.info(f"Screenshot saved: {filepath}")
            return BrowserResult(
                success=True,
                action="screenshot",
                url=self._page.url,
                title=await self._page.title(),
                screenshot_path=str(filepath),
                screenshot_base64=b64
            )
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
            return BrowserResult(
                success=False,
                action="screenshot",
                error=str(e)
            )

    async def get_text(self, selector: str = "body") -> BrowserResult:
        """Get text content from page or element
        
        Args:
            selector: CSS selector (default: body)
            
        Returns:
            BrowserResult with text content
        """
        await self._ensure_started()
        
        try:
            element = await self._page.query_selector(selector)
            if element:
                text = await element.inner_text()
                return BrowserResult(
                    success=True,
                    action="get_text",
                    url=self._page.url,
                    content=text
                )
            else:
                return BrowserResult(
                    success=False,
                    action="get_text",
                    error=f"Element not found: {selector}"
                )
        except Exception as e:
            logger.error(f"Get text failed: {e}")
            return BrowserResult(
                success=False,
                action="get_text",
                error=str(e)
            )

    async def get_html(self, selector: str = "html") -> BrowserResult:
        """Get HTML content from page or element
        
        Args:
            selector: CSS selector (default: html)
            
        Returns:
            BrowserResult with HTML content
        """
        await self._ensure_started()
        
        try:
            element = await self._page.query_selector(selector)
            if element:
                html = await element.inner_html()
                return BrowserResult(
                    success=True,
                    action="get_html",
                    url=self._page.url,
                    content=html
                )
            else:
                return BrowserResult(
                    success=False,
                    action="get_html",
                    error=f"Element not found: {selector}"
                )
        except Exception as e:
            logger.error(f"Get HTML failed: {e}")
            return BrowserResult(
                success=False,
                action="get_html",
                error=str(e)
            )

    async def click(self, selector: str, timeout: int = 5000) -> BrowserResult:
        """Click an element
        
        Args:
            selector: CSS selector
            timeout: Timeout in milliseconds
            
        Returns:
            BrowserResult
        """
        await self._ensure_started()
        
        try:
            await self._page.click(selector, timeout=timeout)
            await self._page.wait_for_load_state("load")
            
            return BrowserResult(
                success=True,
                action="click",
                url=self._page.url,
                title=await self._page.title()
            )
        except Exception as e:
            logger.error(f"Click failed: {e}")
            return BrowserResult(
                success=False,
                action="click",
                error=str(e)
            )

    async def fill(self, selector: str, value: str) -> BrowserResult:
        """Fill a form field
        
        Args:
            selector: CSS selector
            value: Value to fill
            
        Returns:
            BrowserResult
        """
        await self._ensure_started()
        
        try:
            await self._page.fill(selector, value)
            return BrowserResult(
                success=True,
                action="fill",
                url=self._page.url
            )
        except Exception as e:
            logger.error(f"Fill failed: {e}")
            return BrowserResult(
                success=False,
                action="fill",
                error=str(e)
            )

    async def type_text(self, selector: str, text: str, delay: int = 50) -> BrowserResult:
        """Type text into an element (with realistic delay)
        
        Args:
            selector: CSS selector
            text: Text to type
            delay: Delay between keystrokes in ms
            
        Returns:
            BrowserResult
        """
        await self._ensure_started()
        
        try:
            await self._page.type(selector, text, delay=delay)
            return BrowserResult(
                success=True,
                action="type",
                url=self._page.url
            )
        except Exception as e:
            logger.error(f"Type failed: {e}")
            return BrowserResult(
                success=False,
                action="type",
                error=str(e)
            )

    async def press_key(self, key: str) -> BrowserResult:
        """Press a keyboard key
        
        Args:
            key: Key to press (e.g., 'Enter', 'Tab', 'Escape')
            
        Returns:
            BrowserResult
        """
        await self._ensure_started()
        
        try:
            await self._page.keyboard.press(key)
            return BrowserResult(
                success=True,
                action="press_key",
                url=self._page.url
            )
        except Exception as e:
            logger.error(f"Press key failed: {e}")
            return BrowserResult(
                success=False,
                action="press_key",
                error=str(e)
            )

    async def run_javascript(self, script: str) -> BrowserResult:
        """Run JavaScript on the page
        
        Args:
            script: JavaScript code to execute
            
        Returns:
            BrowserResult with result in content field
        """
        await self._ensure_started()
        
        try:
            result = await self._page.evaluate(script)
            return BrowserResult(
                success=True,
                action="run_javascript",
                url=self._page.url,
                content=str(result) if result is not None else None
            )
        except Exception as e:
            logger.error(f"JavaScript execution failed: {e}")
            return BrowserResult(
                success=False,
                action="run_javascript",
                error=str(e)
            )

    async def wait_for_selector(self, selector: str, timeout: int = 30000) -> BrowserResult:
        """Wait for an element to appear
        
        Args:
            selector: CSS selector
            timeout: Timeout in milliseconds
            
        Returns:
            BrowserResult
        """
        await self._ensure_started()
        
        try:
            await self._page.wait_for_selector(selector, timeout=timeout)
            return BrowserResult(
                success=True,
                action="wait_for_selector",
                url=self._page.url
            )
        except Exception as e:
            logger.error(f"Wait for selector failed: {e}")
            return BrowserResult(
                success=False,
                action="wait_for_selector",
                error=str(e)
            )

    async def scroll(self, direction: str = "down", amount: int = 500) -> BrowserResult:
        """Scroll the page
        
        Args:
            direction: 'up' or 'down'
            amount: Pixels to scroll
            
        Returns:
            BrowserResult
        """
        await self._ensure_started()
        
        try:
            scroll_amount = amount if direction == "down" else -amount
            await self._page.evaluate(f"window.scrollBy(0, {scroll_amount})")
            return BrowserResult(
                success=True,
                action="scroll",
                url=self._page.url
            )
        except Exception as e:
            logger.error(f"Scroll failed: {e}")
            return BrowserResult(
                success=False,
                action="scroll",
                error=str(e)
            )

    async def get_links(self) -> BrowserResult:
        """Get all links on the page
        
        Returns:
            BrowserResult with links in content field (JSON)
        """
        await self._ensure_started()
        
        try:
            links = await self._page.evaluate("""
                () => Array.from(document.querySelectorAll('a')).map(a => ({
                    text: a.innerText.trim().slice(0, 100),
                    href: a.href
                })).filter(l => l.href && l.href.startsWith('http'))
            """)
            import json
            return BrowserResult(
                success=True,
                action="get_links",
                url=self._page.url,
                content=json.dumps(links, indent=2)
            )
        except Exception as e:
            logger.error(f"Get links failed: {e}")
            return BrowserResult(
                success=False,
                action="get_links",
                error=str(e)
            )

    def get_status(self) -> Dict[str, Any]:
        """Get browser status"""
        return {
            "started": self._started,
            "headless": self.headless,
            "current_url": self._page.url if self._page else None,
            "screenshots_dir": str(self.screenshots_dir),
            "playwright_available": PLAYWRIGHT_AVAILABLE
        }


# Singleton instance for the application
_browser_instance: Optional[BrowserAutomation] = None


def get_browser() -> BrowserAutomation:
    """Get or create the browser automation instance"""
    global _browser_instance
    if _browser_instance is None:
        _browser_instance = BrowserAutomation(headless=True)
    return _browser_instance


async def close_browser():
    """Close the browser instance"""
    global _browser_instance
    if _browser_instance:
        await _browser_instance.stop()
        _browser_instance = None
