"""Общий браузер Хаты: один Chromium на сервере, картинка кадрами по WebSocket, управление от обоих.

Запуск: python -m screen.app (env: BOT_TOKEN, ALLOWED_IDS, PORT).
"""
import asyncio
import base64
import json
import logging
import os
import time
from urllib.parse import quote_plus, urlparse

from aiohttp import WSMsgType, web
from playwright.async_api import async_playwright

from hub import auth

log = logging.getLogger("screen")
PHONE = {"width": 412, "height": 860}
DESKTOP = {"width": 1280, "height": 720}
PHONE_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Mobile Safari/537.36")
HOME = "https://www.youtube.com/"
IDLE_CLOSE = 600
KEYS = {"Enter", "Backspace", "Tab", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Delete", "Space"}


def search_url(query, current):
    """Поиск там, где сейчас сидим: на YouTube ищем в YouTube, на RuTube в RuTube, иначе DuckDuckGo."""
    q = quote_plus(query)
    host = urlparse(current).netloc
    if "youtube.com" in host:
        return f"https://{host}/results?search_query={q}"
    if "rutube.ru" in host:
        return f"https://rutube.ru/search/?query={q}"
    if "vkvideo.ru" in host:
        return f"https://vkvideo.ru/?q={q}"
    return f"https://duckduckgo.com/?q={q}&kl=ru-ru"


class Screen:
    def __init__(self):
        self.clients = {}
        self.pw = self.browser = self.ctx = self.page = self.cdp = None
        self.mode = "phone"
        self.lock = asyncio.Lock()
        self.last_seen = time.time()
        self.frame = None
        self.sending = set()

    @property
    def size(self):
        return PHONE if self.mode == "phone" else DESKTOP

    async def ensure(self):
        async with self.lock:
            if self.page and not self.page.is_closed():
                return
            if not self.pw:
                self.pw = await async_playwright().start()
            if not self.browser or not self.browser.is_connected():
                proxy = {"server": os.environ["SCREEN_PROXY"]} if os.environ.get("SCREEN_PROXY") else None
                self.browser = await self.pw.chromium.launch(proxy=proxy, args=[
                    "--autoplay-policy=no-user-gesture-required", "--disable-dev-shm-usage", "--no-sandbox",
                    "--lang=ru-RU"])
            await self._open_context(HOME)

    async def _open_context(self, url):
        if self.ctx:
            await self.ctx.close()
        phone = self.mode == "phone"
        self.ctx = await self.browser.new_context(
            viewport=self.size, device_scale_factor=2 if phone else 1, is_mobile=phone, has_touch=phone,
            user_agent=PHONE_UA if phone else None, locale="ru-RU", timezone_id="Europe/Moscow")
        self.page = await self.ctx.new_page()
        self.page.on("framenavigated", lambda f: asyncio.create_task(self.push_nav()) if f == self.page.main_frame else None)
        self.page.on("popup", lambda p: asyncio.create_task(self._adopt_popup(p)))
        self.cdp = await self.ctx.new_cdp_session(self.page)
        self.cdp.on("Page.screencastFrame", lambda e: asyncio.create_task(self._on_frame(e)))
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log.warning("переход %s: %s", url, e)
        await self._start_cast()

    async def _start_cast(self):
        w, h = self.size["width"], self.size["height"]
        scale = 2 if self.mode == "phone" else 1
        # каждый второй кадр: около 15 в секунду, чтобы тянул мобильный интернет
        await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 55,
                                                     "maxWidth": w * scale, "maxHeight": h * scale, "everyNthFrame": 2})

    async def _adopt_popup(self, popup):
        # новые вкладки открываем в той же общей странице
        url = popup.url
        await popup.close()
        if url and url != "about:blank":
            await self.goto(url)

    async def _on_frame(self, e):
        try:
            await self.cdp.send("Page.screencastFrameAck", {"sessionId": e["sessionId"]})
        except Exception:
            return
        self.frame = base64.b64decode(e["data"])
        for ws in list(self.clients):
            # медленному зрителю пропускаем кадр, чтобы он не тормозил второго
            if not ws.closed and ws not in self.sending:
                asyncio.create_task(self._send_frame(ws, self.frame))

    async def _send_frame(self, ws, frame):
        self.sending.add(ws)
        try:
            await ws.send_bytes(frame)
        except (ConnectionError, RuntimeError):
            pass
        finally:
            self.sending.discard(ws)

    async def push_nav(self):
        try:
            title = await self.page.title()
        except Exception:
            title = ""
        await self.broadcast({"t": "nav", "url": self.page.url, "title": title, "mode": self.mode,
                              "w": self.size["width"], "h": self.size["height"]})

    async def broadcast(self, msg):
        for ws in list(self.clients):
            if not ws.closed:
                try:
                    await ws.send_json(msg)
                except (ConnectionError, RuntimeError):
                    pass

    async def goto(self, url):
        url = (url or "").strip()
        if not url:
            return
        if " " in url or "." not in url:
            url = search_url(url, self.page.url if self.page else "")
        elif "://" not in url:
            url = "https://" + url
        if not url.startswith(("http://", "https://")):
            return
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            await self.broadcast({"t": "error", "text": f"Не открылось: {type(e).__name__}"})

    async def handle(self, uid, msg):
        kind = msg.get("t")
        w, h = self.size["width"], self.size["height"]
        p = self.page
        if kind in ("tap", "down", "move", "up"):
            x = max(0.0, min(1.0, float(msg.get("x", 0)))) * w
            y = max(0.0, min(1.0, float(msg.get("y", 0)))) * h
            if kind == "tap":
                await p.mouse.click(x, y)
            elif kind == "down":
                await p.mouse.move(x, y)
                await p.mouse.down()
            elif kind == "move":
                await p.mouse.move(x, y)
            else:
                await p.mouse.move(x, y)
                await p.mouse.up()
        elif kind == "scroll":
            x = float(msg.get("x", .5)) * w
            y = float(msg.get("y", .5)) * h
            await p.mouse.move(x, y)
            await p.mouse.wheel(float(msg.get("dx", 0)), max(-3000.0, min(3000.0, float(msg.get("dy", 0)))))
        elif kind == "text":
            await p.keyboard.insert_text(str(msg.get("text", ""))[:500])
        elif kind == "key" and msg.get("key") in KEYS:
            await p.keyboard.press(" " if msg["key"] == "Space" else msg["key"])
        elif kind == "goto":
            await self.goto(str(msg.get("url", ""))[:2000])
        elif kind == "back":
            await p.go_back(timeout=15000)
        elif kind == "forward":
            await p.go_forward(timeout=15000)
        elif kind == "reload":
            await p.reload(timeout=30000)
        elif kind == "video":
            state = await p.evaluate("""(act) => {
                const v = [...document.querySelectorAll('video')].sort((a, b) => b.clientWidth - a.clientWidth)[0];
                if (!v) return null;
                if (act === 'toggle') { v.paused ? v.play() : v.pause(); }
                if (act === 'back') v.currentTime = Math.max(0, v.currentTime - 10);
                if (act === 'fwd') v.currentTime = v.currentTime + 10;
                return {paused: v.paused, time: v.currentTime};
            }""", msg.get("act", "toggle"))
            if state is None:
                await self.broadcast({"t": "error", "text": "На странице нет видео"})
        elif kind == "mode" and msg.get("mode") in ("phone", "desktop") and msg["mode"] != self.mode:
            url = p.url
            self.mode = msg["mode"]
            await self._open_context(url)
            await self.push_nav()
        elif kind == "pointer":
            await self.broadcast({"t": "pointer", "uid": uid, "x": msg.get("x"), "y": msg.get("y")})

    async def url_watch(self):
        # YouTube и другие SPA меняют адрес без перехода: следим сами
        last = None
        while True:
            await asyncio.sleep(1)
            try:
                url = self.page.url if self.page and not self.page.is_closed() else None
            except Exception:
                url = None
            if url and url != last and self.clients:
                last = url
                await self.push_nav()

    async def idle_reaper(self):
        while True:
            await asyncio.sleep(60)
            if not self.clients and self.browser and time.time() - self.last_seen > IDLE_CLOSE:
                async with self.lock:
                    log.info("никого нет, закрываю браузер")
                    await self.browser.close()
                    self.browser = self.ctx = self.page = self.cdp = None


def make_app(token, allowed):
    screen = Screen()

    async def ws_handler(request):
        uid = auth.read_session(request.query.get("t", ""), token, allowed)
        if uid is None:
            raise web.HTTPUnauthorized(text="нет входа")
        ws = web.WebSocketResponse(heartbeat=25, max_msg_size=64 * 1024)
        await ws.prepare(request)
        await ws.send_json({"t": "hello", "starting": screen.page is None})
        try:
            await screen.ensure()
        except Exception as e:
            log.exception("браузер не запустился")
            await ws.send_json({"t": "error", "text": f"Браузер не запустился: {type(e).__name__}",
                                "detail": str(e)[:1500]})
            await ws.close()
            return ws
        screen.clients[ws] = uid
        screen.last_seen = time.time()
        await screen.push_nav()
        await screen.broadcast({"t": "viewers", "uids": sorted(set(screen.clients.values()))})
        if screen.frame:
            await ws.send_bytes(screen.frame)
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                    if isinstance(data, dict):
                        await screen.handle(uid, data)
                except Exception as e:
                    log.warning("команда %s: %s", msg.data[:100], e)
        finally:
            screen.clients.pop(ws, None)
            screen.last_seen = time.time()
            await screen.broadcast({"t": "viewers", "uids": sorted(set(screen.clients.values()))})
        return ws

    async def diag(request):
        if auth.read_session(request.query.get("t", ""), token, allowed) is None:
            raise web.HTTPUnauthorized()
        import platform
        import shutil
        import sys
        info = {"python": sys.version, "platform": platform.platform(), "uid": os.getuid() if hasattr(os, "getuid") else None,
                "apt": bool(shutil.which("apt-get")), "browsers_path": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")}
        try:
            info["chromium"] = screen.pw.chromium.executable_path if screen.pw else None
        except Exception as e:
            info["chromium"] = repr(e)
        return web.json_response(info)

    async def health(request):
        return web.json_response({"ok": True, "browser": screen.page is not None, "viewers": len(screen.clients)})

    @web.middleware
    async def cors(request, handler):
        resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    async def on_start(app):
        app["reaper"] = asyncio.create_task(screen.idle_reaper())
        app["urlwatch"] = asyncio.create_task(screen.url_watch())

    app = web.Application(middlewares=[cors])
    app["screen"] = screen
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/health", health)
    app.router.add_get("/diag", diag)
    async def on_stop(app):
        app["reaper"].cancel()
        app["urlwatch"].cancel()
        if screen.browser:
            await screen.browser.close()
        if screen.pw:
            await screen.pw.stop()

    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from hub import settings

    async def serve():
        runner = web.AppRunner(make_app(settings.BOT_TOKEN, settings.ALLOWED_IDS))
        await runner.setup()
        # как у хаба: bothost может перевести веб на 3000
        for port in sorted({settings.PORT, 8080, 3000}):
            await web.TCPSite(runner, "0.0.0.0", port).start()
        log.info("общий браузер слушает %s", sorted({settings.PORT, 8080, 3000}))
        await asyncio.Event().wait()

    asyncio.run(serve())


if __name__ == "__main__":
    main()
