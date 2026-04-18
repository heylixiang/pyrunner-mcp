from __future__ import annotations

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from ..config import settings

_playwright: Playwright | None = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_page: Page | None = None


async def _ensure_browser() -> Browser:
    global _playwright, _browser
    if _browser is None or not _browser.is_connected():
        _playwright = await async_playwright().start()
        kwargs = {}
        if settings.browser_cdp_headers:
            kwargs["headers"] = settings.browser_cdp_headers
        _browser = await _playwright.chromium.connect_over_cdp(settings.browser_cdp_url, **kwargs)
    return _browser


async def get_context() -> BrowserContext:
    global _context
    browser = await _ensure_browser()
    if _context is None:
        contexts = browser.contexts
        _context = contexts[0] if contexts else await browser.new_context()
        _context.set_default_timeout(settings.BROWSER_TIMEOUT)
    return _context


async def get_page() -> Page:
    global _page
    context = await get_context()
    if _page is None or _page.is_closed():
        pages = context.pages
        _page = pages[0] if pages else await context.new_page()
    return _page


async def set_page(page: Page) -> None:
    global _page
    _page = page


async def close_browser() -> None:
    global _playwright, _browser, _context, _page
    if _context is not None:
        await _context.close()
        _context = None
        _page = None
    if _browser is not None:
        await _browser.close()
        _browser = None
    if _playwright is not None:
        await _playwright.stop()
        _playwright = None
