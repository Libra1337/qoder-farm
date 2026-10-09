"""
浏览器适配层(Playwright 驱动 Chromium/Chrome)

为什么有这一层(2026-10):
  注册机原先直接用 DrissionPage。但 Chrome 154+ 的 `--headless=new` 不再写
  DevToolsActivePort 文件、浏览器级 CDP 端点改到 /devtools/browser/<id>,
  DrissionPage 4.1.1.4(及 5.0.0b1)启动期发现逻辑拿到的 ws 路径不对 → 握手 404
  (实测:显式端口/地址/attach 到自启 Chrome 全部失败)。于是服务器只能靠 Xvfb 假装有显示。
  Playwright 官方 headless 直接可用(实测 0.7s 连上,元素查询/鼠标拖拽正常),
  故改用它,彻底去掉 Xvfb 依赖。

本模块对外暴露与 DrissionPage 同名的 `ChromiumOptions` / `ChromiumPage` 及少量错误类型,
只实现注册机 + 滑块实际用到的子集(见下),使 registrar/core 仅需改 import 一行。
  - ChromiumOptions: set_argument / set_local_port(空操作) / set_window_size /
    set_user_agent / set_proxy / set_user_data_path
  - ChromiumPage: get / url / html / title / ele / eles / wait.ele_displayed /
    wait.load_start / set.window.hide|show(空操作) / actions(move_to/hold/move/release) /
    get_tabs / quit
  - Element: attr / text / tag / states.is_displayed / click / input / clear / value /
    rect.size / rect.location / parent

依赖:playwright(浏览器用系统 Chrome,channel 默认 chrome,可用 REG_BROWSER_CHANNEL 覆盖)。
"""
from __future__ import annotations

import os
import random
import re
import time
from typing import Any
from urllib.parse import urlsplit


class ElementNotFoundError(Exception):
    """定位失败(与 DrissionPage.errors.ElementNotFoundError 同名,便于就地替换)。"""


class WaitTimeoutError(Exception):
    """等待超时(与 DrissionPage.errors.WaitTimeoutError 同名)。"""


def _translate(selector: str) -> str:
    """DrissionPage 定位符 → Playwright 选择器。"""
    s = (selector or "").strip()
    if s.startswith("css:"):
        return s[4:]
    if s.startswith("text:"):
        return "text=" + s[5:]
    if s.startswith("xpath:"):
        return "xpath=" + s[6:]
    return s


def _proxy_dict(url: str) -> dict[str, str]:
    """http://user:pass@host:port → Playwright proxy 配置。"""
    parts = urlsplit(url if "://" in url else f"http://{url}")
    server = f"{parts.scheme}://{parts.hostname}"
    if parts.port:
        server += f":{parts.port}"
    proxy: dict[str, str] = {"server": server}
    if parts.username:
        proxy["username"] = parts.username
    if parts.password:
        proxy["password"] = parts.password
    return proxy


class _Rect:
    def __init__(self, box: dict | None) -> None:
        box = box or {}
        self.size = (float(box.get("width", 0.0)), float(box.get("height", 0.0)))
        self.location = (float(box.get("x", 0.0)), float(box.get("y", 0.0)))


class _States:
    def __init__(self, locator) -> None:
        self._loc = locator

    @property
    def is_displayed(self) -> bool:
        try:
            return bool(self._loc.is_visible())
        except Exception:
            return False


class Element:
    """DrissionPage 元素的子集包装(内部持 Playwright Locator)。"""

    def __init__(self, locator, page: "ChromiumPage") -> None:
        self._loc = locator
        self._page = page
        self.states = _States(locator)

    # ---- 属性 ----
    def attr(self, name: str) -> str | None:
        try:
            return self._loc.get_attribute(name)
        except Exception:
            return None

    @property
    def text(self) -> str:
        try:
            return self._loc.inner_text()
        except Exception:
            return ""

    @property
    def tag(self) -> str:
        try:
            return str(self._loc.evaluate("el => el.tagName.toLowerCase()"))
        except Exception:
            return ""

    @property
    def value(self) -> str:
        try:
            return self._loc.input_value()
        except Exception:
            return self.attr("value") or ""

    @property
    def rect(self) -> _Rect:
        try:
            return _Rect(self._loc.bounding_box())
        except Exception:
            return _Rect(None)

    # ---- 交互 ----
    def click(self) -> None:
        self._loc.click(timeout=15000)

    def clear(self) -> None:
        try:
            self._loc.clear(timeout=5000)
        except Exception:
            pass

    def input(self, value: str, clear: bool = True) -> None:
        """输入文本:聚焦后逐键输入(触发 React 受控输入所需的键盘事件,且更像真人)。"""
        if clear:
            self.clear()
        try:
            self._loc.click(timeout=5000)
        except Exception:
            pass
        try:
            self._loc.press_sequentially(value, delay=random.uniform(25, 70))
        except Exception:
            self._loc.fill(value)

    def parent(self) -> "Element":
        return Element(self._loc.locator("xpath=.."), self._page)

    def ele(self, selector: str, timeout: float | None = None) -> "Element | None":
        return self._page.ele_in(self._loc, selector, timeout)

    def eles(self, selector: str, timeout: float | None = None) -> list["Element"]:
        return self._page.eles_in(self._loc, selector, timeout)


class _Waiter:
    def __init__(self, page: "ChromiumPage") -> None:
        self._page = page

    def ele_displayed(self, selector: str, timeout: float = 10) -> "Element | None":
        el = self._page.ele(selector, timeout=timeout, state="visible")
        if el is None:
            raise WaitTimeoutError(f"等待元素显示超时: {selector}")
        return el

    def load_start(self, timeout: float = 30) -> None:
        try:
            self._page._page.wait_for_load_state("load", timeout=timeout * 1000)
        except Exception:
            pass


class _Window:
    """DrissionPage 的窗口控制。headless 下无窗口,故为日志化空操作。"""

    def __init__(self, page: "ChromiumPage") -> None:
        self._page = page

    def hide(self) -> None:
        # Playwright 无"隐藏窗口"API;headless 本就无窗口,headful 下保持可见即可
        pass

    def show(self) -> None:
        try:
            self._page._page.bring_to_front()
        except Exception:
            pass


class _Set:
    def __init__(self, page: "ChromiumPage") -> None:
        self.window = _Window(page)


class ActionChains:
    """DrissionPage page.actions 的子集:move_to / hold / move(相对) / release。

    Playwright 的 mouse.move 是绝对坐标,这里自行跟踪当前位置以复刻相对位移语义。
    duration(秒)映射为 steps(线性插值步数)。
    """

    def __init__(self, page: "ChromiumPage") -> None:
        self._page = page
        self._x = 0.0
        self._y = 0.0

    def move_to(self, el: "Element | None" = None, x: float | None = None, y: float | None = None) -> "ActionChains":
        if el is not None:
            box = el._loc.bounding_box()
            if box:
                x = box["x"] + box["width"] / 2
                y = box["y"] + box["height"] / 2
        if x is not None and y is not None:
            self._x, self._y = float(x), float(y)
            self._page._page.mouse.move(self._x, self._y)
        return self

    def hold(self) -> "ActionChains":
        self._page._page.mouse.down()
        return self

    def move(self, dx: float, dy: float, duration: float = 0.0, **_: Any) -> "ActionChains":
        self._x += float(dx)
        self._y += float(dy)
        steps = max(1, int(duration * 120)) if duration else 1
        self._page._page.mouse.move(self._x, self._y, steps=steps)
        return self

    def release(self) -> "ActionChains":
        self._page._page.mouse.up()
        return self

    # DrissionPage 常用别名
    def up(self) -> "ActionChains":
        return self.release()

    def down(self) -> "ActionChains":
        return self.hold()


class ChromiumOptions:
    """DrissionPage ChromiumOptions 的子集;内部收集 Playwright 启动参数。"""

    def __init__(self) -> None:
        self.args: list[str] = []
        self.window_size: tuple[int, int] | None = None
        self.user_agent: str | None = None
        self.proxy: str | None = None
        self.user_data_path: str | None = None
        self.local_port: int | None = None  # Playwright 自行管理,仅兼容保留

    def set_argument(self, arg: str) -> None:
        self.args.append(arg)

    def set_local_port(self, port: int) -> None:
        self.local_port = port

    def set_window_size(self, w: int, h: int) -> None:
        self.window_size = (int(w), int(h))

    def set_user_agent(self, ua: str) -> None:
        self.user_agent = ua

    def set_proxy(self, proxy: str) -> None:
        self.proxy = proxy

    def set_user_data_path(self, path: str) -> None:
        self.user_data_path = path


class ChromiumPage:
    """DrissionPage ChromiumPage 的子集;内部为 Playwright 持久化上下文 + 页面。"""

    def __init__(self, options: ChromiumOptions | str | None = None, headless: bool | None = None) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover
            raise ImportError("浏览器适配层需要 playwright:pip install playwright") from e

        if isinstance(options, str):  # DrissionPage 允许直接传地址,这里不支持
            raise ValueError("browser 适配层不支持直接传入 CDP 地址,请传 ChromiumOptions")
        self._opts = options or ChromiumOptions()

        # headless 决策:默认跟随 SLIDER_MANUAL(无人值守=0 → headless);REG_HEADFUL=1 强制有头
        if headless is None:
            manual = (os.getenv("SLIDER_MANUAL", "1") or "1").strip() != "0"
            headless = not manual
            if os.getenv("REG_HEADFUL", "").strip() in ("1", "true", "yes"):
                headless = False
        self.headless = bool(headless)

        channel = os.getenv("REG_BROWSER_CHANNEL", "chrome").strip() or "chrome"
        args = list(self._opts.args)
        if sys_platform_linux():
            args += ["--no-sandbox", "--disable-dev-shm-usage"]
        if self._opts.window_size:
            args.append(f"--window-size={self._opts.window_size[0]},{self._opts.window_size[1]}")

        self._pw = sync_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "headless": self.headless,
            "channel": channel,
            "args": args,
        }
        if self._opts.proxy:
            launch_kwargs["proxy"] = _proxy_dict(self._opts.proxy)
        ctx_kwargs: dict[str, Any] = {
            "user_data_dir": self._opts.user_data_path or temp_profile(),
            "viewport": {"width": (self._opts.window_size or (1280, 800))[0],
                         "height": (self._opts.window_size or (1280, 800))[1]},
        }
        if self._opts.user_agent:
            ctx_kwargs["user_agent"] = self._opts.user_agent
        try:
            self._context = self._pw.chromium.launch_persistent_context(**launch_kwargs, **ctx_kwargs)
        except Exception:
            self._pw.stop()
            raise
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self.wait = _Waiter(self)
        self.set = _Set(self)
        self.actions = ActionChains(self)

    # ---- 导航 / 属性 ----
    def get(self, url: str, retry: int = 1) -> None:
        self._page.goto(url, wait_until="domcontentloaded", timeout=60000)

    @property
    def url(self) -> str:
        return self._page.url

    @property
    def html(self) -> str:
        try:
            return self._page.content()
        except Exception:
            return ""

    @property
    def title(self) -> str:
        try:
            return self._page.title()
        except Exception:
            return ""

    # ---- 元素定位 ----
    def ele(self, selector: str, timeout: float | None = None, state: str = "attached") -> Element | None:
        return self.ele_in(None, selector, timeout, state)

    def eles(self, selector: str, timeout: float | None = None) -> list[Element]:
        return self.eles_in(None, selector, timeout)

    def ele_in(self, scope, selector: str, timeout: float | None = None, state: str = "attached") -> Element | None:
        sel = _translate(selector)
        ms = 10000 if timeout is None else max(1, int(timeout * 1000))
        root = scope if scope is not None else self._page
        try:
            root.wait_for_selector(sel, timeout=ms, state=state)
        except Exception:
            return None
        loc = (scope.locator(sel) if scope is not None else self._page.locator(sel)).first
        return Element(loc, self)

    def eles_in(self, scope, selector: str, timeout: float | None = None) -> list[Element]:
        sel = _translate(selector)
        ms = 10000 if timeout is None else max(1, int(timeout * 1000))
        root = scope if scope is not None else self._page
        try:
            root.wait_for_selector(sel, timeout=ms, state="attached")
        except Exception:
            return []
        loc = scope.locator(sel) if scope is not None else self._page.locator(sel)
        return [Element(loc.nth(i), self) for i in range(loc.count())]

    def get_tabs(self) -> list:
        try:
            return list(self._context.pages)
        except Exception:
            return [self._page]

    # ---- 收尾 ----
    def quit(self) -> None:
        try:
            self._context.close()
        except Exception:
            pass
        try:
            self._pw.stop()
        except Exception:
            pass


def sys_platform_linux() -> bool:
    import sys
    return sys.platform.startswith("linux")


def temp_profile() -> str:
    import tempfile
    return tempfile.mkdtemp(prefix="qoder_reg_")


__all__ = ["ChromiumOptions", "ChromiumPage", "Element", "ElementNotFoundError", "WaitTimeoutError"]