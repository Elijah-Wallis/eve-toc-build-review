from __future__ import annotations

import asyncio
import logging
import random
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeoutError
import html2text

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("mcp-stealth-browser")

try:
    from playwright_stealth import stealth_async
except (ImportError, AttributeError):
    logger.warning("Stealth module not available - running in standard mode")

    async def stealth_async(*_args: object, **_kwargs: object) -> None:
        return None


mcp = FastMCP("stealth-browser")


class BrowserState:
    """Holds browser/page across MCP tool calls."""

    def __init__(self) -> None:
        self.playwright = None
        self.browser = None
        self.context = None
        self.page: Optional[Page] = None
        self.headless: bool = False
        self.user_agent: Optional[str] = None
        self.launched = False


STATE = BrowserState()

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edg/126.0.0.0",
]


def _error(message: str) -> str:
    logger.error(message)
    return message


async def _ensure_playwright_running() -> bool:
    """Return True if browser/page are available."""
    if STATE.page and STATE.page.is_closed() is False:
        return True

    if STATE.browser and STATE.browser.is_connected() is False:
        STATE.browser = None
        STATE.context = None
        STATE.page = None

    return False


@mcp.tool()
async def browser_launch(headless: bool = False, state_path: str | None = None) -> str:
    """Start Chromium with stealth setup and keep it alive across tool calls."""
    if await _ensure_playwright_running():
        return f"Browser already running (headful={not STATE.headless})."

    try:
        logger.info("Launching Chromium (headful=%s)", not headless)
        STATE.playwright = await async_playwright().start()

        # Keep automation fingerprints low-noise by reducing automation signals.
        ua = random.choice(USER_AGENTS)
        STATE.browser = await STATE.playwright.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        storage_state = None
        if state_path:
            candidate = Path(state_path)
            if candidate.exists():
                storage_state = str(candidate)
                logger.info("Loading storage state from: %s", candidate)

        if storage_state is None:
            logger.info("No storage state provided or missing; creating fresh context.")

        STATE.context = await STATE.browser.new_context(
            user_agent=ua,
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1365, "height": 768},
            storage_state=storage_state,
        )
        STATE.page = await STATE.context.new_page()
        STATE.headless = headless
        STATE.user_agent = ua

        # Apply anti-detection script for navigator.webdriver early.
        await STATE.page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined })"
        )

        await stealth_async(STATE.page)

        # Optional hardening against automation fingerprints.
        await STATE.page.add_init_script(
            """
            delete window.cdc_adoQpoasnfa76pfcZLmcfl;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl__props;
            """
        )

        logger.info("Browser launched with UA: %s", ua)
        return "Browser launched"
    except Exception as exc:  # pragma: no cover - runtime dependent behavior
        return _error(f"Failed to launch browser: {exc}")


@mcp.tool()
async def browser_save_state(path: str) -> str:
    """Persist the current storage state to disk."""
    if not await _ensure_playwright_running():
        return "Browser is not launched. Call browser_launch() first."

    try:
        state_path = Path(path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        await STATE.context.storage_state(path=str(state_path))
        return f"Saved storage state to {state_path}"
    except Exception as exc:
        return _error(f"Failed to save storage state: {exc}")


@mcp.tool()
async def browser_navigate(url: str) -> str:
    """Navigate current page to URL using networkidle when possible."""
    if not await _ensure_playwright_running():
        return "Browser is not launched. Call browser_launch() first."

    try:
        logger.info("Navigating to: %s", url)
        try:
            await STATE.page.goto(url, wait_until="networkidle", timeout=25000)
            return f"Navigated to {url}"
        except PlaywrightTimeoutError:
            logger.warning("networkidle timeout; retrying with domcontentloaded")
            await STATE.page.goto(url, wait_until="domcontentloaded", timeout=20000)
        return f"Navigated to {url}"
    except Exception as exc:
        return _error(f"Navigation failed for '{url}': {exc}")


@mcp.tool()
async def browser_click(selector: str) -> str:
    """Click an element after human-like delay."""
    if not await _ensure_playwright_running():
        return "Browser is not launched. Call browser_launch() first."

    try:
        logger.info("Click request: selector=%s", selector)
        locator = STATE.page.locator(selector)
        if await locator.count() == 0:
            return _error(f"Element '{selector}' not found")

        await locator.first.wait_for(state="visible", timeout=8000)
        await asyncio.sleep(random.uniform(0.5, 1.5))

        await locator.first.scroll_into_view_if_needed()
        await locator.first.click(timeout=12000)
        return f"Clicked '{selector}'"
    except PlaywrightTimeoutError:
        return _error(f"Element '{selector}' not visible")
    except Exception as exc:
        return _error(f"Failed clicking '{selector}': {exc}")


@mcp.tool()
async def browser_type(selector: str, text: str, delay_ms: int = 50) -> str:
    """Type with randomized per-character delay for human-like input."""
    if not await _ensure_playwright_running():
        return "Browser is not launched. Call browser_launch() first."

    try:
        logger.info("Type request: selector=%s", selector)
        locator = STATE.page.locator(selector)
        if await locator.count() == 0:
            return _error(f"Element '{selector}' not found")

        await locator.first.wait_for(state="visible", timeout=8000)
        await locator.first.click()
        await locator.first.fill("")

        for char in text:
            delay = random.randint(max(20, delay_ms - 15), delay_ms + 15)
            await STATE.page.keyboard.insert_text(char)
            await asyncio.sleep(delay / 1000)
        return f"Typed text into '{selector}'"
    except PlaywrightTimeoutError:
        return _error(f"Element '{selector}' not visible")
    except Exception as exc:
        return _error(f"Failed typing into '{selector}': {exc}")


@mcp.tool()
async def browser_upload(selector: str, file_path: str) -> str:
    """Upload a file to an <input type='file'> element."""
    if not await _ensure_playwright_running():
        return "Browser is not launched. Call browser_launch() first."

    try:
        logger.info("Upload request: selector=%s file=%s", selector, file_path)
        upload_path = Path(file_path)
        if not upload_path.exists():
            return _error(f"File not found: {file_path}")
        locator = STATE.page.locator(selector)
        if await locator.count() == 0:
            return _error(f"Element '{selector}' not found")

        await locator.first.wait_for(state="visible", timeout=8000)
        await locator.first.set_input_files(file_path)
        return f"Uploaded '{file_path}' to '{selector}'"
    except PlaywrightTimeoutError:
        return _error(f"Element '{selector}' not visible")
    except Exception as exc:
        return _error(f"Failed uploading '{file_path}' into '{selector}': {exc}")


@mcp.tool()
async def browser_get_content(selector: str = "body") -> str:
    """Return markdown text for selected HTML area or whole page."""
    if not await _ensure_playwright_running():
        return "Browser is not launched. Call browser_launch() first."

    try:
        logger.info("Content request: selector=%s", selector)
        locator = STATE.page.locator(selector)
        if await locator.count() == 0:
            return _error(f"Element '{selector}' not found")

        html = await locator.first.inner_html()
        text = html2text.html2text(html)
        return text.strip()
    except Exception as exc:
        return _error(f"Failed getting content for '{selector}': {exc}")


@mcp.tool()
async def browser_screenshot(path: str) -> str:
    """Capture and save a full-page screenshot."""
    if not await _ensure_playwright_running():
        return "Browser is not launched. Call browser_launch() first."

    try:
        logger.info("Screenshot request: path=%s", path)
        screenshot_path = Path(path)
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        await STATE.page.screenshot(path=str(screenshot_path), full_page=True)
        return f"Screenshot saved to {screenshot_path}"
    except Exception as exc:
        return _error(f"Failed screenshot to '{path}': {exc}")


@mcp.tool()
async def browser_evaluate(script: str) -> str:
    """Evaluate JavaScript in the page context."""
    if not await _ensure_playwright_running():
        return "Browser is not launched. Call browser_launch() first."

    try:
        logger.info("Evaluate request (script): %d chars", len(script))
        result = await STATE.page.evaluate(script)
        return str(result)
    except Exception as exc:
        return _error(f"Failed to evaluate script: {exc}")


@mcp.tool()
async def browser_close() -> str:
    """Close browser resources. Useful between sessions."""
    if not await _ensure_playwright_running():
        return "Browser is not running"

    try:
        if STATE.context is not None:
            await STATE.context.close()
        if STATE.browser is not None:
            await STATE.browser.close()
        if STATE.playwright is not None:
            await STATE.playwright.stop()

        STATE.playwright = None
        STATE.browser = None
        STATE.context = None
        STATE.page = None
        STATE.launched = False
        return "Browser closed"
    except Exception as exc:
        return _error(f"Failed to close browser: {exc}")


if __name__ == "__main__":
    mcp.run(transport="stdio")
