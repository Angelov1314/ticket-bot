"""
Bot core logic — platform-agnostic, callback-based logging.
Designed to be driven by the FastAPI server.
"""

import asyncio
import random
import time
import base64
import io
from typing import Callable, Awaitable, Optional
from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PWTimeout
from pathlib import Path

LogFn = Callable[[str, str], Awaitable[None]]  # (level, message)


# ─────────────────────── Platform Adapters ────────────────────────────────────

class DamaiAdapter:
    name = "大麦网"
    BUY_BTN   = "button:has-text('立即购买'), button:has-text('立即预订'), .btn-buy, [class*='buy-btn']"
    SOLD_OUT  = "[class*='sold-out'], [class*='soldOut'], button:has-text('已售罄'), button:has-text('暂时缺货')"
    SESSION   = "[class*='perform-item'], [class*='session-item'], .perform_show_item"
    SEAT      = "[class*='ticket-item'], [class*='price-item'], .ticket_price_item"
    CONFIRM   = "button:has-text('确认'), button:has-text('下一步'), [class*='confirm-btn']"
    SUBMIT    = "button:has-text('提交订单'), button:has-text('去付款')"
    VIEWER_ADD= "button:has-text('添加观演人'), button:has-text('选择观演人')"
    NAME_IN   = "[placeholder*='姓名'], input[name*='name'], input[name*='realName']"
    PHONE_IN  = "[placeholder*='手机'], input[type='tel'], input[name*='phone']"
    ID_IN     = "[placeholder*='身份证'], input[name*='idCard'], input[name*='idNo']"

    def __init__(self, config: dict):
        self.config = config

    async def goto(self, page: Page):
        await page.goto(self.config["ticket_url"], wait_until="domcontentloaded", timeout=15000)

    async def is_on_sale(self, page: Page) -> bool:
        try:
            btn = page.locator(self.BUY_BTN).first
            if await btn.count() == 0:
                return False
            sold = await page.locator(self.SOLD_OUT).count()
            disabled = await btn.is_disabled()
            return not disabled and sold == 0
        except Exception:
            return False

    async def get_status_text(self, page: Page) -> str:
        """Return human-readable ticket status from page."""
        try:
            sold = await page.locator(self.SOLD_OUT).count()
            if sold > 0:
                return "已售罄"
            btn = page.locator(self.BUY_BTN).first
            if await btn.count() == 0:
                return "未找到购票按钮"
            if await btn.is_disabled():
                return "购票按钮不可用（未开票或暂停）"
            text = await btn.inner_text()
            return f"可购票: [{text.strip()}]"
        except Exception as e:
            return f"状态检测异常: {e}"

    async def select_session(self, page: Page):
        pref = self.config["ticket"].get("session", "").strip()
        sessions = page.locator(self.SESSION)
        count = await sessions.count()
        if count == 0:
            return
        if pref:
            for i in range(count):
                t = await sessions.nth(i).inner_text()
                if pref in t and not await sessions.nth(i).is_disabled():
                    await sessions.nth(i).click()
                    await page.wait_for_timeout(300)
                    return
        for i in range(count):
            if not await sessions.nth(i).is_disabled():
                await sessions.nth(i).click()
                await page.wait_for_timeout(300)
                return

    async def select_seat(self, page: Page):
        pref = self.config["ticket"].get("seat_type", "").strip()
        seats = page.locator(self.SEAT)
        count = await seats.count()
        if count == 0:
            return
        if pref:
            for i in range(count):
                t = await seats.nth(i).inner_text()
                if pref in t and not await seats.nth(i).is_disabled():
                    await seats.nth(i).click()
                    await page.wait_for_timeout(200)
                    return
        for i in range(count):
            if not await seats.nth(i).is_disabled():
                await seats.nth(i).click()
                await page.wait_for_timeout(200)
                return

    async def click_buy(self, page: Page) -> bool:
        try:
            await page.locator(self.BUY_BTN).first.click(timeout=3000)
            return True
        except Exception:
            return False

    async def fill_order(self, page: Page):
        b = self.config.get("buyer", {})
        try:
            add = page.locator(self.VIEWER_ADD)
            if await add.count() > 0:
                await add.first.click()
                await page.wait_for_timeout(500)
            n = page.locator(self.NAME_IN).first
            if await n.count() > 0:
                await n.fill(b.get("name", ""))
            p = page.locator(self.PHONE_IN).first
            if await p.count() > 0:
                await p.fill(b.get("phone", ""))
            if b.get("id_card"):
                id_ = page.locator(self.ID_IN).first
                if await id_.count() > 0:
                    await id_.fill(b["id_card"])
        except Exception:
            pass

    async def submit_order(self, page: Page) -> bool:
        try:
            c = page.locator(self.CONFIRM)
            if await c.count() > 0:
                await c.first.click()
                await page.wait_for_timeout(500)
            await page.locator(self.SUBMIT).first.click(timeout=5000)
            return True
        except Exception:
            return False


class MaoyanAdapter:
    name = "猫眼演出"
    def __init__(self, config: dict):
        self.config = config
    async def goto(self, page: Page):
        await page.goto(self.config["ticket_url"], wait_until="domcontentloaded", timeout=15000)
    async def is_on_sale(self, page: Page) -> bool:
        btn = page.locator("button:has-text('立即购票'), .buy-btn, [class*='buyBtn']").first
        if await btn.count() == 0: return False
        return not await btn.is_disabled()
    async def get_status_text(self, page: Page) -> str:
        if await self.is_on_sale(page): return "可购票"
        return "暂不可购"
    async def select_session(self, page: Page): pass
    async def select_seat(self, page: Page): pass
    async def click_buy(self, page: Page) -> bool:
        try:
            await page.click("button:has-text('立即购票'), .buy-btn", timeout=3000)
            return True
        except Exception: return False
    async def fill_order(self, page: Page):
        b = self.config.get("buyer", {})
        try:
            await page.fill("input[placeholder*='姓名']", b.get("name", ""))
            await page.fill("input[placeholder*='手机']", b.get("phone", ""))
        except Exception: pass
    async def submit_order(self, page: Page) -> bool:
        try:
            await page.click("button:has-text('提交'), button:has-text('去支付')", timeout=5000)
            return True
        except Exception: return False


ADAPTERS = {"damai": DamaiAdapter, "maoyan": MaoyanAdapter}


# ─────────────────────── Bot State ────────────────────────────────────────────

class BotState:
    IDLE       = "idle"
    RUNNING    = "running"
    FOUND      = "found"
    SUCCESS    = "success"
    STOPPED    = "stopped"
    ERROR      = "error"


class TicketBot:
    def __init__(self, config: dict, log_fn: LogFn, screenshot_fn: Callable[[str], Awaitable[None]]):
        self.config      = config
        self.log         = log_fn
        self.on_screenshot = screenshot_fn
        self.state       = BotState.IDLE
        self.retries     = 0
        self.simulate    = config.get("simulate", False)
        self._task: Optional[asyncio.Task] = None
        self._browser_ctx = None

    async def start(self):
        if self.state == BotState.RUNNING:
            return
        self.state = BotState.RUNNING
        self.retries = 0
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._browser_ctx:
            try:
                await self._browser_ctx.close()
            except Exception:
                pass
            self._browser_ctx = None
        self.state = BotState.STOPPED
        await self.log("warn", "抢票任务已停止")

    async def _run(self):
        platform = self.config["platform"]
        AdapterCls = ADAPTERS.get(platform, DamaiAdapter)
        adapter = AdapterCls(self.config)

        await self.log("info", f"平台: {adapter.name}")
        await self.log("info", f"URL: {self.config['ticket_url']}")
        if self.simulate:
            await self.log("warn", "⚡ 模拟模式：仅监控，不实际下单")

        user_data = Path(self.config["browser"]["user_data_dir"]).expanduser()
        user_data.mkdir(parents=True, exist_ok=True)

        max_retries  = self.config["safety"]["max_retries"]
        poll_interval= self.config["timing"]["poll_interval"]
        jitter       = self.config["safety"]["request_jitter"]

        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(user_data),
                headless=self.config["browser"]["headless"],
                slow_mo=self.config["browser"]["slow_mo"],
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--window-size=1280,800",
                ],
                ignore_default_args=["--enable-automation"],
            )
            self._browser_ctx = ctx
            await ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get:()=>undefined});"
                "window.chrome={runtime:{}};"
            )
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.set_extra_http_headers({
                "Accept-Language": "zh-CN,zh;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            })

            while self.retries < max_retries:
                self.retries += 1
                try:
                    await self.log("info", f"第 {self.retries}/{max_retries} 次检测...")
                    await adapter.goto(page)
                    await page.wait_for_load_state("domcontentloaded")
                    await page.wait_for_timeout(500)

                    # Screenshot
                    shot = await page.screenshot(full_page=False)
                    b64  = base64.b64encode(shot).decode()
                    await self.on_screenshot(b64)

                    # Status check
                    status_text = await adapter.get_status_text(page)
                    await self.log("info", f"页面状态: {status_text}")

                    on_sale = await adapter.is_on_sale(page)
                    if not on_sale:
                        sleep = poll_interval + random.uniform(0, jitter)
                        await self.log("debug", f"未开售，{sleep:.1f}s 后重试...")
                        await asyncio.sleep(sleep)
                        continue

                    # Found ticket!
                    self.state = BotState.FOUND
                    await self.log("success", "🎉 发现有票！开始下单流程...")

                    if self.simulate:
                        await self.log("warn", "模拟模式：已检测到购票按钮，模拟流程结束")
                        await self.log("success", "✅ 模拟完成 — 真实运行将在此自动点击购买并填单")
                        self.state = BotState.SUCCESS
                        break

                    # Real purchase flow
                    await adapter.select_session(page)
                    await adapter.select_seat(page)
                    shot = await page.screenshot(full_page=False)
                    await self.on_screenshot(base64.b64encode(shot).decode())

                    clicked = await adapter.click_buy(page)
                    if not clicked:
                        await self.log("warn", "点击购买失败，重试...")
                        continue

                    try:
                        await page.wait_for_url(
                            lambda u: any(k in u for k in ["order", "buy", "checkout", "confirm"]),
                            timeout=8000
                        )
                    except PWTimeout:
                        await self.log("warn", "未跳转订单页，重试...")
                        continue

                    await self.log("info", "进入订单页，填写信息...")
                    await adapter.fill_order(page)
                    await page.wait_for_timeout(500)

                    shot = await page.screenshot(full_page=False)
                    await self.on_screenshot(base64.b64encode(shot).decode())

                    ok = await adapter.submit_order(page)
                    if ok:
                        self.state = BotState.SUCCESS
                        await self.log("success", "✅ 订单已提交！请在浏览器中完成付款！")
                        await asyncio.sleep(300)
                        break
                    else:
                        await self.log("warn", "提交订单失败，重试...")

                except asyncio.CancelledError:
                    raise
                except PWTimeout:
                    await self.log("warn", "页面超时，重试...")
                    await asyncio.sleep(poll_interval)
                except Exception as e:
                    await self.log("error", f"异常: {e}")
                    await asyncio.sleep(poll_interval)

            if self.state == BotState.RUNNING:
                self.state = BotState.STOPPED
                await self.log("warn", f"已达最大重试次数 ({max_retries})，停止。")
