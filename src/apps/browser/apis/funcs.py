from __future__ import annotations

import base64

from lib.sandbox_api import SandboxAPI

from playwright.async_api import Page

from .browser import get_page, get_context, set_page

api = SandboxAPI()


@api.function
async def navigate(url: str) -> dict:
    """导航到指定 URL，返回页面标题和最终 URL。"""
    page = await get_page()
    response = await page.goto(url, wait_until="domcontentloaded")
    try:
        title = await page.title()
    except Exception:
        title = ""
    return {
        "url": page.url,
        "title": title,
        "status": response.status if response else None,
    }


@api.function
async def get_page_info() -> dict:
    """获取当前页面的标题和 URL。"""
    page = await get_page()
    try:
        title = await page.title()
    except Exception:
        title = ""
    return {
        "url": page.url,
        "title": title,
    }


@api.function
async def get_text_content(selector: str = "body") -> str:
    """获取页面或指定元素的文本内容。默认返回整个 body 的文本。"""
    page = await get_page()
    element = await page.query_selector(selector)
    if element is None:
        return ""
    return await element.inner_text() or ""


@api.function
async def get_html(selector: str = "body", outer: bool = False) -> str:
    """获取指定元素的 HTML。outer=True 返回 outerHTML，否则返回 innerHTML。"""
    page = await get_page()
    if outer:
        element = await page.query_selector(selector)
        if element is None:
            return ""
        return await element.evaluate("el => el.outerHTML")
    return await page.inner_html(selector)


@api.function
async def screenshot(selector: str | None = None, full_page: bool = False) -> str:
    """截图并返回 base64 编码的 PNG 图片。可指定 selector 截取某个元素，或 full_page=True 截取整个页面。"""
    page = await get_page()
    if selector:
        element = await page.query_selector(selector)
        if element is None:
            return ""
        data = await element.screenshot(type="png")
    else:
        data = await page.screenshot(type="png", full_page=full_page)
    return base64.b64encode(data).decode()


@api.function
async def save_screenshot(selector: str | None = None, full_page: bool = False, name: str | None = None) -> str:
    """将截图保存到当前工作目录，文件名为时间戳，返回保存路径。"""
    import time
    import os
    if name is None:
        name = f"screenshot_{int(time.time())}.png"
    else:
        name = f"screenshot_{name}.png"
    path = os.path.join(os.getcwd(), name)
    page = await get_page()
    if selector:
        element = await page.query_selector(selector)
        if element is None:
            return ""
        await element.screenshot(path=path, type="png")
    else:
        await page.screenshot(path=path, type="png", full_page=full_page)

    return path


@api.function
async def click(selector: str) -> dict:
    """点击指定选择器的元素。"""
    page = await get_page()
    await page.click(selector)
    return {"clicked": selector, "url": page.url}


@api.function
async def fill(selector: str, value: str) -> dict:
    """在输入框中填入文本。"""
    page = await get_page()
    await page.fill(selector, value)
    return {"filled": selector, "value": value}


@api.function
async def select_option(selector: str, value: str) -> list[str]:
    """在下拉框中选择指定值，返回被选中的值列表。"""
    page = await get_page()
    return await page.select_option(selector, value)


@api.function
async def press_key(key: str, selector: str | None = None) -> dict:
    """按下键盘按键。可选指定 selector 在某个元素上触发。常用: Enter, Tab, Escape, ArrowDown 等。"""
    page = await get_page()
    if selector:
        await page.press(selector, key)
    else:
        await page.keyboard.press(key)
    return {"key": key, "selector": selector}


@api.function
async def query_elements(selector: str) -> list[dict]:
    """查询所有匹配选择器的元素，返回 tag、文本、href 等属性信息（最多 50 个）。"""
    page = await get_page()
    elements = await page.query_selector_all(selector)
    results = []
    for el in elements[:50]:
        tag = await el.evaluate("el => el.tagName.toLowerCase()")
        text = (await el.inner_text()).strip()[:200]
        attrs = await el.evaluate(
            """el => {
                const obj = {};
                for (const attr of el.attributes) obj[attr.name] = attr.value;
                return obj;
            }"""
        )
        results.append({"tag": tag, "text": text, "attributes": attrs})
    return results


@api.function
async def evaluate_js(expression: str) -> str:
    """在页面中执行 JavaScript 表达式，返回序列化后的结果。"""
    page = await get_page()
    result = await page.evaluate(expression)
    return str(result)


@api.function
async def wait_for_selector(selector: str, state: str = "visible", timeout: int = 30000) -> bool:
    """等待元素出现。state 可选: visible, hidden, attached, detached。返回是否成功。"""
    page = await get_page()
    try:
        await page.wait_for_selector(selector, state=state, timeout=timeout)
        return True
    except Exception:
        return False


@api.function
async def go_back() -> dict:
    """后退到上一页，返回当前页面信息。"""
    page = await get_page()
    await page.go_back(wait_until="domcontentloaded")
    return {"url": page.url, "title": await page.title()}


@api.function
async def go_forward() -> dict:
    """前进到下一页，返回当前页面信息。"""
    page = await get_page()
    await page.go_forward(wait_until="domcontentloaded")
    return {"url": page.url, "title": await page.title()}


@api.function
async def get_cookies() -> list[dict]:
    """获取当前页面的所有 cookies。"""
    page = await get_page()
    return await page.context.cookies()


@api.function
async def clear_cookies(keep_names: list[str] | None = None) -> dict:
    """清除 cookies，可选保留指定名称的 cookie。返回清除数量和保留数量。"""
    page = await get_page()
    context = page.context
    all_cookies = await context.cookies()
    if keep_names:
        keep = [c for c in all_cookies if c["name"] in keep_names]
    else:
        keep = []
    await context.clear_cookies()
    if keep:
        await context.add_cookies(keep)
    return {"cleared": len(all_cookies) - len(keep), "kept": len(keep)}


@api.function
async def scroll(direction: str = "down", amount: int = 500) -> dict:
    """滚动页面。direction: up/down/left/right，amount: 滚动像素数。"""
    page = await get_page()
    delta_x, delta_y = 0, 0
    if direction == "down":
        delta_y = amount
    elif direction == "up":
        delta_y = -amount
    elif direction == "right":
        delta_x = amount
    elif direction == "left":
        delta_x = -amount
    await page.mouse.wheel(delta_x, delta_y)
    scroll_pos = await page.evaluate("() => ({ x: window.scrollX, y: window.scrollY })")
    return {"direction": direction, "amount": amount, "scroll_position": scroll_pos}


@api.function
async def hover(selector: str) -> dict:
    """鼠标悬停在指定元素上。"""
    page = await get_page()
    await page.hover(selector)
    return {"hovered": selector}


@api.function
async def get_input_value(selector: str) -> str:
    """获取输入框当前的值。"""
    page = await get_page()
    return await page.input_value(selector)


@api.function
async def list_pages() -> list[dict]:
    """列出浏览器中所有打开的页面（tab），返回 index、title、url。"""
    context = await get_context()
    return [{"index": i, "title": await p.title(), "url": p.url} for i, p in enumerate(context.pages)]


@api.function
async def switch_page(index: int) -> dict:
    """切换到指定 index 的页面并将其激活，返回页面信息。先用 list_pages 获取 index。"""
    context = await get_context()
    pages = context.pages
    if index < 0 or index >= len(pages):
        raise IndexError(f"page index {index} out of range (total: {len(pages)})")
    page = pages[index]
    await page.bring_to_front()
    await set_page(page)
    return {"index": index, "url": page.url, "title": await page.title()}


@api.function
async def get_iframes() -> list:
    """返回页面中所有 iframe 元素信息。"""
    page = await get_page()
    return await page.locator("iframe").evaluate_all("""
        els => els.map((el, index) => ({
            index,
            id: el.id || "",
            name: el.name || "",
            title: el.title || "",
            src: el.src || "",
            className: el.className || "",
            outerHTML: el.outerHTML
        }))
    """)


@api.function
async def click_in_frame(frame_selector: str, inner_selector: str, timeout: int = 10000) -> dict:
    """点击指定 iframe 内部元素。"""
    page = await get_page()

    element = await page.query_selector(frame_selector)
    if element is None:
        return {
            "clicked": False,
            "frame_selector": frame_selector,
            "inner_selector": inner_selector,
            "reason": "iframe not found",
        }

    frame = await element.content_frame()
    if frame is None:
        return {
            "clicked": False,
            "frame_selector": frame_selector,
            "inner_selector": inner_selector,
            "reason": "iframe has no content_frame",
        }

    target = frame.locator(inner_selector).first
    await target.wait_for(state="visible", timeout=timeout)
    await target.click(timeout=timeout)

    return {
        "clicked": True,
        "frame_selector": frame_selector,
        "inner_selector": inner_selector,
    }


@api.function
async def get_frame_info() -> list:
    """返回 page.frames 里的所有 frame 信息。"""
    page = await get_page()
    return [
        {
            "index": i,
            "name": frame.name,
            "url": frame.url,
            "is_main_frame": frame == page.main_frame,
        }
        for i, frame in enumerate(page.frames)
    ]


@api.function
async def get_frame_html(frame_selector: str) -> str:
    """返回指定 iframe 对应 frame 的 HTML。"""
    page = await get_page()

    element = await page.query_selector(frame_selector)
    if element is None:
        return ""

    frame = await element.content_frame()
    if frame is None:
        return ""

    return await frame.content()


@api.function
async def get_page_ins() -> Page:
    """返回 playwright Page 对象实例，供更灵活的操作使用。"""
    return await get_page()


@api.function
async def has_cf_checkbox(selector: str = "#captcha-element") -> bool:
    """检查页面中是否存在指定选择器的可见元素（自动搜索所有 iframe）。"""
    page = await get_page()
    if await page.query_selector(selector) is not None:
        return True
    for frame in page.frames:
        if frame != page.main_frame and await frame.query_selector(selector) is not None:
            return True
    return False


@api.function
async def has_cf_iframe_checkbox() -> bool:
    """检查页面中是否存在 Cloudflare 相关的 checkbox 验证（通常在 iframe 内）。具体实现可能需要根据实际情况调整选择器。这里是一个示例，检查是否存在常见的 Cloudflare 验证元素。"""
    page = await get_page()
    area = await page.query_selector(".main-content .ch-description +div")
    return area is not None


@api.function
async def click_cf_checkbox(selector: str = "#captcha-element", timeout: int = 10000) -> bool:
    """点击 checkbox 元素中心（自动搜索所有 iframe）。成功返回 True，未找到返回 False。"""
    page = await get_page()

    async def _click(loc) -> bool:
        await loc.wait_for(state="visible", timeout=timeout)
        box = await loc.bounding_box()
        if box:
            await loc.click(position={"x": box["width"] / 2, "y": box["height"] / 2})
        else:
            await loc.click()
        return True

    if await page.query_selector(selector) is not None:
        return await _click(page.locator(selector).first)

    for frame in page.frames:
        if frame != page.main_frame and await frame.query_selector(selector) is not None:
            return await _click(frame.locator(selector).first)

    return False


@api.function
async def click_cf_iframe_checkbox() -> bool:
    """点击 Cloudflare 验证相关的 checkbox（通常在 iframe 内）。具体实现可能需要根据实际情况调整选择器。这里是一个示例，尝试点击常见的 Cloudflare 验证元素。成功返回 True，未找到返回 False。"""
    page = await get_page()
    area = await page.query_selector(".main-content .ch-description +div")
    if area is not None:
        box = await area.bounding_box()
        if box:
            await area.click(position={"x": 10, "y": box["height"] / 2})
            return True
    return False


