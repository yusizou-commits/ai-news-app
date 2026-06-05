#!/usr/bin/env python3
"""AI 日报 — 启动脚本。运行: python3 launch.py"""
import subprocess
import sys
import os
import time
import webbrowser
import threading

REQUIRED = ["flask", "feedparser", "requests", "googletrans==4.0.0rc1"]
PORT = 7788


def install_deps():
    print("🔧 检查并安装依赖...")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", *REQUIRED],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    print("✅ 依赖已就绪")


def open_browser():
    time.sleep(1.5)
    url = f"http://localhost:{PORT}"
    print(f"🌐 在浏览器中打开: {url}")
    webbrowser.open(url)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    install_deps()
    threading.Thread(target=open_browser, daemon=True).start()
    print(f"🚀 启动 AI 日报服务器，端口 {PORT}...")
    print("   按 Ctrl+C 关闭\n")
    from app import app
    app.run(port=PORT, debug=False, threaded=True)
