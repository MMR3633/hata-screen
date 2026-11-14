"""Общий браузер Хаты. Только браузер: Telegram здесь не опрашивается."""
import subprocess
import sys


def ensure_browser():
    # если площадка проигнорировала Dockerfile, ставим Chromium сами при первом запуске
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as e:
        print("Chromium не запускается, ставлю:", type(e).__name__, flush=True)
        subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], check=False)


if __name__ == "__main__":
    ensure_browser()
    from screen.app import main
    main()
