"""
演唱会抢票 Web Server
FastAPI + WebSocket real-time logging
"""

import asyncio
import json
import yaml
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from bot_core import TicketBot, BotState

app = FastAPI(title="演唱会抢票平台")

# ─── Static files ────────────────────────────────────────────────────────────
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# ─── Global state ────────────────────────────────────────────────────────────
_bot: Optional[TicketBot] = None
_ws_clients: list[WebSocket] = []
_latest_screenshot: Optional[str] = None   # base64 PNG
_log_buffer: list[dict] = []               # recent logs for late-joiners


def load_config() -> dict:
    base = Path(__file__).parent
    p = base / "config.yaml"
    if not p.exists():
        p = base / "config.example.yaml"
    with open(p) as f:
        return yaml.safe_load(f)


def save_config(data: dict):
    p = Path(__file__).parent / "config.yaml"
    with open(p, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


# ─── WebSocket broadcast ─────────────────────────────────────────────────────

async def broadcast(msg: dict):
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)


async def log_fn(level: str, message: str):
    entry = {"type": "log", "level": level, "message": message,
             "time": datetime.now().strftime("%H:%M:%S")}
    _log_buffer.append(entry)
    if len(_log_buffer) > 200:
        _log_buffer.pop(0)
    await broadcast(entry)


async def screenshot_fn(b64: str):
    global _latest_screenshot
    _latest_screenshot = b64
    await broadcast({"type": "screenshot", "data": b64})


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html = (static_dir / "index.html").read_text()
    return HTMLResponse(html)


@app.get("/api/config")
async def get_config():
    return JSONResponse(load_config())


@app.post("/api/config")
async def update_config(body: dict):
    cfg = load_config()
    # Deep merge
    for k, v in body.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    save_config(cfg)
    return {"ok": True}


@app.post("/api/start")
async def start_bot(body: dict = None):
    global _bot, _log_buffer
    _log_buffer.clear()

    if _bot and _bot.state == BotState.RUNNING:
        return {"ok": False, "error": "Bot is already running"}

    cfg = load_config()
    if body:
        for k, v in body.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v

    _bot = TicketBot(cfg, log_fn, screenshot_fn)
    await _bot.start()
    await log_fn("info", "=== 抢票任务启动 ===")
    await broadcast({"type": "status", "state": BotState.RUNNING})
    return {"ok": True}


@app.post("/api/stop")
async def stop_bot():
    global _bot
    if _bot:
        await _bot.stop()
        await broadcast({"type": "status", "state": BotState.STOPPED})
    return {"ok": True}


@app.get("/api/status")
async def get_status():
    return {
        "state": _bot.state if _bot else BotState.IDLE,
        "retries": _bot.retries if _bot else 0,
        "simulate": _bot.simulate if _bot else False,
        "has_screenshot": _latest_screenshot is not None,
    }


@app.get("/api/screenshot")
async def get_screenshot():
    if _latest_screenshot:
        return {"data": _latest_screenshot}
    return {"data": None}


@app.post("/api/login")
async def login(body: dict = None):
    """Open browser for manual QR code login."""
    cfg = load_config()
    platform = cfg.get("platform", "damai")
    urls = {
        "damai":     "https://passport.damai.cn/login",
        "maoyan":    "https://passport.maoyan.com/login",
        "showstart": "https://www.showstart.com/user/login",
    }
    url = urls.get(platform, urls["damai"])

    from playwright.async_api import async_playwright
    user_data = Path(cfg["browser"]["user_data_dir"]).expanduser()
    user_data.mkdir(parents=True, exist_ok=True)

    async def open_login():
        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(user_data),
                headless=False,
            )
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(url)
            await log_fn("info", f"浏览器已打开登录页，请扫码登录: {url}")
            await asyncio.sleep(180)   # 3 min timeout
            await ctx.close()
            await log_fn("success", "登录状态已保存！")

    asyncio.create_task(open_login())
    return {"ok": True, "url": url}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)

    # Send buffered logs to new client
    for entry in _log_buffer:
        await ws.send_json(entry)
    if _latest_screenshot:
        await ws.send_json({"type": "screenshot", "data": _latest_screenshot})
    if _bot:
        await ws.send_json({"type": "status", "state": _bot.state})

    try:
        while True:
            await ws.receive_text()   # keep alive
    except WebSocketDisconnect:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


# ─── Bot state polling (push updates to WS clients) ─────────────────────────

@app.on_event("startup")
async def start_polling():
    async def poll():
        last = None
        while True:
            await asyncio.sleep(1)
            if _bot:
                state = _bot.state
                if state != last:
                    last = state
                    await broadcast({"type": "status", "state": state})
    asyncio.create_task(poll())


# ─── Entry ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8765))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
