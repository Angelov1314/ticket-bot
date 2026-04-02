#!/usr/bin/env python3
"""
演唱会抢票脚本 v1.0
支持平台: 大麦网 / 猫眼演出 / 秀动
仅供个人购票使用

Usage:
    python ticket_bot.py                    # 使用 config.yaml
    python ticket_bot.py --url <URL>        # 指定演出URL
    python ticket_bot.py --login            # 扫码登录并保存状态
"""

import asyncio
import random
import sys
import time
import yaml
import logging
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from dateutil import parser as dateparser

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PWTimeout

console = Console()
log = logging.getLogger("ticket-bot")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# ─────────────────────────── Platform Adapters ───────────────────────────────

class PlatformAdapter:
    """Base class for platform-specific logic."""
    name = "base"

    def __init__(self, config: dict):
        self.config = config

    async def goto_ticket_page(self, page: Page):
        await page.goto(self.config["ticket_url"], wait_until="domcontentloaded")

    async def is_on_sale(self, page: Page) -> bool:
        raise NotImplementedError

    async def select_session(self, page: Page):
        pass  # Optional: pick a specific session

    async def select_seat_type(self, page: Page):
        pass  # Optional: pick a ticket tier

    async def set_quantity(self, page: Page):
        pass  # Optional: set qty > 1

    async def click_buy(self, page: Page) -> bool:
        raise NotImplementedError

    async def fill_order(self, page: Page) -> bool:
        raise NotImplementedError

    async def submit_order(self, page: Page) -> bool:
        raise NotImplementedError


class DamaiAdapter(PlatformAdapter):
    """大麦网 (damai.cn) adapter."""
    name = "damai"

    SELECTORS = {
        "buy_btn":       ".btn-buy, [class*='buy-btn'], button:has-text('立即购买'), button:has-text('立即预订')",
        "sold_out":      "[class*='sold-out'], [class*='soldOut'], button:has-text('已售罄'), button:has-text('暂时缺货')",
        "session_item":  "[class*='perform-item'], [class*='session-item']",
        "seat_item":     "[class*='ticket-item'], [class*='price-item']",
        "confirm_btn":   "button:has-text('确认'), button:has-text('下一步'), [class*='confirm-btn']",
        "submit_btn":    "button:has-text('提交订单'), button:has-text('去付款')",
        "real_name_sel": "[placeholder*='姓名'], input[name*='name']",
        "phone_sel":     "[placeholder*='手机'], input[type='tel']",
        "id_sel":        "[placeholder*='身份证'], input[name*='idCard']",
        "viewer_add":    "button:has-text('添加观演人'), button:has-text('选择观演人')",
    }

    async def is_on_sale(self, page: Page) -> bool:
        try:
            # Check if buy button is present and enabled
            btn = page.locator(self.SELECTORS["buy_btn"]).first
            if await btn.count() == 0:
                return False
            is_disabled = await btn.is_disabled()
            sold_out = await page.locator(self.SELECTORS["sold_out"]).count()
            return not is_disabled and sold_out == 0
        except Exception:
            return False

    async def select_session(self, page: Page):
        pref = self.config["ticket"].get("session", "").strip()
        sessions = page.locator(self.SELECTORS["session_item"])
        count = await sessions.count()
        if count == 0:
            return
        if pref:
            for i in range(count):
                text = await sessions.nth(i).inner_text()
                if pref in text:
                    await sessions.nth(i).click()
                    await page.wait_for_timeout(300)
                    return
        # Default: click first non-disabled session
        for i in range(count):
            if not await sessions.nth(i).is_disabled():
                await sessions.nth(i).click()
                await page.wait_for_timeout(300)
                return

    async def select_seat_type(self, page: Page):
        pref = self.config["ticket"].get("seat_type", "").strip()
        seats = page.locator(self.SELECTORS["seat_item"])
        count = await seats.count()
        if count == 0:
            return
        if pref:
            for i in range(count):
                text = await seats.nth(i).inner_text()
                if pref in text:
                    if not await seats.nth(i).is_disabled():
                        await seats.nth(i).click()
                        await page.wait_for_timeout(200)
                        return
        # Default: first available
        for i in range(count):
            if not await seats.nth(i).is_disabled():
                await seats.nth(i).click()
                await page.wait_for_timeout(200)
                return

    async def click_buy(self, page: Page) -> bool:
        try:
            btn = page.locator(self.SELECTORS["buy_btn"]).first
            await btn.click(timeout=3000)
            return True
        except Exception as e:
            log.debug(f"click_buy failed: {e}")
            return False

    async def fill_order(self, page: Page) -> bool:
        """Fill in buyer info on order page."""
        buyer = self.config.get("buyer", {})
        try:
            # Try to add viewer if needed
            add_btn = page.locator(self.SELECTORS["viewer_add"])
            if await add_btn.count() > 0:
                await add_btn.first.click()
                await page.wait_for_timeout(500)

            # Fill name
            name_inp = page.locator(self.SELECTORS["real_name_sel"]).first
            if await name_inp.count() > 0:
                await name_inp.fill(buyer.get("name", ""))

            # Fill phone
            phone_inp = page.locator(self.SELECTORS["phone_sel"]).first
            if await phone_inp.count() > 0:
                await phone_inp.fill(buyer.get("phone", ""))

            # Fill ID card if present
            if buyer.get("id_card"):
                id_inp = page.locator(self.SELECTORS["id_sel"]).first
                if await id_inp.count() > 0:
                    await id_inp.fill(buyer["id_card"])

            return True
        except Exception as e:
            log.warning(f"fill_order error: {e}")
            return False

    async def submit_order(self, page: Page) -> bool:
        try:
            # Confirm intermediate step if any
            confirm = page.locator(self.SELECTORS["confirm_btn"])
            if await confirm.count() > 0:
                await confirm.first.click()
                await page.wait_for_timeout(500)

            # Submit
            submit = page.locator(self.SELECTORS["submit_btn"]).first
            await submit.click(timeout=5000)
            return True
        except Exception as e:
            log.warning(f"submit_order error: {e}")
            return False


class MaoyanAdapter(PlatformAdapter):
    """猫眼演出 adapter."""
    name = "maoyan"

    async def is_on_sale(self, page: Page) -> bool:
        btn = page.locator("button:has-text('立即购票'), .buy-btn, [class*='buyBtn']").first
        if await btn.count() == 0:
            return False
        return not await btn.is_disabled()

    async def click_buy(self, page: Page) -> bool:
        try:
            await page.click("button:has-text('立即购票'), .buy-btn", timeout=3000)
            return True
        except Exception:
            return False

    async def fill_order(self, page: Page) -> bool:
        buyer = self.config.get("buyer", {})
        try:
            await page.fill("input[placeholder*='姓名']", buyer.get("name", ""))
            await page.fill("input[placeholder*='手机']", buyer.get("phone", ""))
            return True
        except Exception:
            return False

    async def submit_order(self, page: Page) -> bool:
        try:
            await page.click("button:has-text('提交'), button:has-text('去支付')", timeout=5000)
            return True
        except Exception:
            return False


class ShowstartAdapter(PlatformAdapter):
    """秀动 (showstart.com) adapter."""
    name = "showstart"

    async def is_on_sale(self, page: Page) -> bool:
        btn = page.locator(".btn-buy, button:has-text('购票'), [class*='buyBtn']").first
        if await btn.count() == 0:
            return False
        return not await btn.is_disabled()

    async def click_buy(self, page: Page) -> bool:
        try:
            await page.click(".btn-buy, button:has-text('购票')", timeout=3000)
            return True
        except Exception:
            return False

    async def fill_order(self, page: Page) -> bool:
        buyer = self.config.get("buyer", {})
        try:
            await page.fill("input[name='name']", buyer.get("name", ""))
            await page.fill("input[name='phone']", buyer.get("phone", ""))
            return True
        except Exception:
            return False

    async def submit_order(self, page: Page) -> bool:
        try:
            await page.click("button:has-text('提交订单')", timeout=5000)
            return True
        except Exception:
            return False


ADAPTERS = {
    "damai":     DamaiAdapter,
    "maoyan":    MaoyanAdapter,
    "showstart": ShowstartAdapter,
}

# ─────────────────────────── Core Bot Logic ──────────────────────────────────

class TicketBot:
    def __init__(self, config: dict):
        self.config = config
        self.platform = config["platform"]
        self.adapter: PlatformAdapter = ADAPTERS[self.platform](config)
        self.max_retries = config["safety"]["max_retries"]
        self.poll_interval = config["timing"]["poll_interval"]
        self.jitter = config["safety"]["request_jitter"]

    def _jitter_sleep(self) -> float:
        return self.poll_interval + random.uniform(0, self.jitter)

    async def wait_for_start(self):
        """Wait until configured start_time, with countdown."""
        start_str = self.config["timing"].get("start_time", "")
        if not start_str:
            return
        start_dt = dateparser.parse(start_str)
        pre = self.config["timing"].get("pre_start_seconds", 30)
        wake_dt = start_dt.timestamp() - pre

        now = time.time()
        if now >= wake_dt:
            return

        wait_secs = wake_dt - now
        console.print(f"[yellow]开票时间: {start_str}[/yellow]")
        console.print(f"[yellow]将在 {wait_secs:.0f}s 后 ({pre}s 提前量) 开始抢票...[/yellow]")

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                      console=console) as progress:
            task = progress.add_task("等待开票中...", total=None)
            while time.time() < wake_dt:
                remaining = wake_dt - time.time()
                progress.update(task, description=f"距开票还有 {remaining + pre:.0f}s | 正在倒计时...")
                await asyncio.sleep(0.5)

        console.print("[bold green]到时间了！开始抢票！[/bold green]")

    async def run(self):
        user_data = Path(self.config["browser"]["user_data_dir"]).expanduser()
        user_data.mkdir(parents=True, exist_ok=True)

        console.print(Panel(
            f"[bold cyan]演唱会抢票脚本[/bold cyan]\n"
            f"平台: [yellow]{self.platform}[/yellow]\n"
            f"URL: {self.config['ticket_url'][:60]}...\n"
            f"购票人: {self.config['buyer']['name']} | {self.config['buyer']['phone']}",
            title="Ticket Bot v1.0"
        ))

        await self.wait_for_start()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch_persistent_context(
                user_data_dir=str(user_data),
                headless=self.config["browser"]["headless"],
                slow_mo=self.config["browser"]["slow_mo"],
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars",
                ],
                ignore_default_args=["--enable-automation"],
            )

            # Stealth: override navigator.webdriver
            await browser.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
            """)

            page = browser.pages[0] if browser.pages else await browser.new_page()
            await page.set_extra_http_headers({
                "Accept-Language": "zh-CN,zh;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            })

            await self._run_loop(page, browser)

    async def _run_loop(self, page: Page, ctx: BrowserContext):
        retries = 0
        success = False

        while retries < self.max_retries and not success:
            try:
                retries += 1
                log.info(f"第 {retries}/{self.max_retries} 次尝试...")

                # Navigate (reload to get fresh stock status)
                await self.adapter.goto_ticket_page(page)
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(300)

                # Check on sale
                on_sale = await self.adapter.is_on_sale(page)
                if not on_sale:
                    log.info("票未开售 / 已售罄，继续等待...")
                    await asyncio.sleep(self._jitter_sleep())
                    continue

                console.print("[bold green]>>> 发现有票！立即下单！[/bold green]")

                # Select session / seat type
                await self.adapter.select_session(page)
                await self.adapter.select_seat_type(page)

                # Click buy
                clicked = await self.adapter.click_buy(page)
                if not clicked:
                    log.warning("点击购买按钮失败，重试...")
                    continue

                # Wait for order page
                try:
                    await page.wait_for_url(
                        lambda url: "order" in url or "buy" in url or "checkout" in url,
                        timeout=8000
                    )
                except PWTimeout:
                    log.warning("未跳转到订单页，重试...")
                    continue

                console.print("[cyan]已进入订单页，填写信息...[/cyan]")

                # Fill order details
                filled = await self.adapter.fill_order(page)
                if not filled:
                    log.warning("填写订单信息失败")

                await page.wait_for_timeout(500)

                # Submit
                submitted = await self.adapter.submit_order(page)
                if submitted:
                    console.print(Panel(
                        "[bold green]订单已提交！请尽快完成支付！[/bold green]",
                        title="SUCCESS"
                    ))
                    self._notify_success()
                    success = True
                    # Keep browser open for manual payment
                    console.print("[yellow]浏览器将保持打开，请手动完成付款...[/yellow]")
                    await asyncio.sleep(300)  # Wait 5 min before auto-close
                else:
                    log.warning("提交订单失败，重试...")

            except PWTimeout:
                log.warning("页面超时，重试...")
                await asyncio.sleep(self._jitter_sleep())
            except Exception as e:
                log.error(f"未知错误: {e}")
                await asyncio.sleep(self._jitter_sleep())

        if not success:
            console.print(f"[red]已达最大重试次数 ({self.max_retries})，抢票结束。[/red]")

    def _notify_success(self):
        if self.config.get("notify", {}).get("sound", True):
            # macOS: play system sound
            subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False)
            # Also print beep
            print("\a")


# ─────────────────────────── Login Helper ────────────────────────────────────

async def do_login(config: dict):
    """Open browser for manual login, then save session."""
    user_data = Path(config["browser"]["user_data_dir"]).expanduser()
    user_data.mkdir(parents=True, exist_ok=True)

    console.print("[cyan]打开浏览器，请手动扫码登录...[/cyan]")
    console.print("[yellow]登录完成后按 Ctrl+C 关闭脚本，登录状态会自动保存。[/yellow]")

    platform_urls = {
        "damai":     "https://passport.damai.cn/login",
        "maoyan":    "https://passport.maoyan.com/login",
        "showstart": "https://www.showstart.com/user/login",
    }
    login_url = platform_urls.get(config["platform"], config["ticket_url"])

    async with async_playwright() as pw:
        browser = await pw.chromium.launch_persistent_context(
            user_data_dir=str(user_data),
            headless=False,
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()
        await page.goto(login_url)
        console.print("[green]请在浏览器中完成登录，然后按 Ctrl+C[/green]")
        try:
            await asyncio.sleep(300)
        except asyncio.CancelledError:
            pass
        await browser.close()
    console.print("[green]登录状态已保存！[/green]")


# ─────────────────────────── Entry Point ─────────────────────────────────────

def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


async def main():
    parser = argparse.ArgumentParser(description="演唱会抢票脚本")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--url", help="演出URL (覆盖配置文件)")
    parser.add_argument("--login", action="store_true", help="打开登录页面")
    parser.add_argument("--platform", help="平台 (damai/maoyan/showstart)")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.url:
        config["ticket_url"] = args.url
    if args.platform:
        config["platform"] = args.platform

    if config["platform"] not in ADAPTERS:
        console.print(f"[red]不支持的平台: {config['platform']}[/red]")
        console.print(f"支持的平台: {', '.join(ADAPTERS.keys())}")
        sys.exit(1)

    if args.login:
        await do_login(config)
        return

    bot = TicketBot(config)
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]已手动停止。[/yellow]")
