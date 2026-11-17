#!/usr/bin/env python3
"""带代理环境变量启动 Antigravity IDE（代替直接双击 exe）。

为什么需要它
------------
agy（Go 程序）和 Electron 主进程都**不读 Windows 系统代理**，只认环境变量；
Electron 主进程的 fetch 还需要 NODE_USE_ENV_PROXY=1 才会走代理。
并且仅设环境变量不够：Chromium 会把 https://127.0.0.1:<本地端口>/ 的 UI 请求
也送进代理，导致白屏 ERR_TIMED_OUT。必须额外加 --proxy-server，
Chromium 才会隐式绕过 loopback。

用法:
    python launch_ide.py                        # 自动发现路径与代理端口
    python launch_ide.py --proxy http://127.0.0.1:7892
    python launch_ide.py --ide "D:\\path\\Antigravity IDE.exe"
    python launch_ide.py --check                # 只报告发现结果，不启动
"""
import argparse
import glob
import os
import socket
import subprocess
import sys

HOME = os.path.expanduser("~")
PROXY_PORTS = [7892, 7890, 7897, 1080, 8889, 2080, 8080, 10808]

CANDIDATES = [
    os.path.join(HOME, "AppData", "Local", "Programs", "Antigravity IDE", "Antigravity IDE.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Antigravity IDE",
                 "Antigravity IDE.exe"),
    os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                 "Antigravity IDE", "Antigravity IDE.exe"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                 "Antigravity IDE", "Antigravity IDE.exe"),
    r"D:\APP\Antigravity IDE新版\Antigravity IDE\Antigravity IDE.exe",
    r"D:\APP\Antigravity IDE\Antigravity IDE.exe",
]

SEARCH_ROOTS = [
    os.path.join(HOME, "AppData", "Local", "Programs"),
    r"D:\APP",
    r"C:\Program Files",
]


def find_ide():
    # 1) 环境变量优先
    env = os.environ.get("ANTIGRAVITY_IDE")
    if env and os.path.isfile(env):
        return env
    # 2) 常见路径
    for p in CANDIDATES:
        if p and os.path.isfile(p):
            return p
    # 3) 浅层搜索（深度 2），避免全盘扫描
    for root in SEARCH_ROOTS:
        if not os.path.isdir(root):
            continue
        for depth1 in glob.glob(os.path.join(root, "*")):
            exe = os.path.join(depth1, "Antigravity IDE.exe")
            if os.path.isfile(exe):
                return exe
            for depth2 in glob.glob(os.path.join(depth1, "*")):
                exe = os.path.join(depth2, "Antigravity IDE.exe")
                if os.path.isfile(exe):
                    return exe
    return None


def port_open(port, timeout=0.35):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def parse_proxy(s):
    """接受 http://127.0.0.1:7892 或 127.0.0.1:7892 或纯端口"""
    if not s:
        return None
    s = s.strip()
    if s.isdigit():
        return "127.0.0.1", int(s)
    s = s.split("://", 1)[-1].rstrip("/")
    if ":" not in s:
        return None
    host, p = s.rsplit(":", 1)
    try:
        return host, int(p)
    except ValueError:
        return None


def resolve_proxy(explicit):
    got = parse_proxy(explicit)
    if got:
        return got
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        got = parse_proxy(os.environ.get(k))
        if got and port_open(got[1]):
            return got
    for p in PROXY_PORTS:
        if port_open(p):
            return "127.0.0.1", p
    return None


def main():
    ap = argparse.ArgumentParser(description="带代理环境变量启动 Antigravity IDE")
    ap.add_argument("--proxy", help="代理地址，如 http://127.0.0.1:7892 或 7892")
    ap.add_argument("--ide", help="Antigravity IDE.exe 的完整路径")
    ap.add_argument("--check", action="store_true", help="只报告发现结果，不启动")
    ap.add_argument("--no-proxy-server", action="store_true",
                    help="不传 --proxy-server（不推荐，会导致本地 UI 白屏）")
    args = ap.parse_args()

    exe = args.ide or find_ide()
    if not exe or not os.path.isfile(exe):
        print("找不到 Antigravity IDE.exe")
        print("请用 --ide 指定，或设置环境变量 ANTIGRAVITY_IDE")
        return 2

    proxy = resolve_proxy(args.proxy)
    if not proxy:
        print("未发现可用代理端口（已尝试 %s）" % PROXY_PORTS)
        print("请先启动代理软件，或用 --proxy 指定，例如 --proxy 127.0.0.1:7892")
        return 1
    host, port = proxy
    url = "http://%s:%d" % (host, port)

    env = os.environ.copy()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
              "http_proxy", "https_proxy", "all_proxy"):
        env[k] = url
    env["NO_PROXY"] = "localhost,127.0.0.1,::1"
    env["no_proxy"] = "localhost,127.0.0.1,::1"
    env["NODE_USE_ENV_PROXY"] = "1"          # 让 Electron 主进程 fetch 走代理
    env["AGY_CLI_DISABLE_AUTO_UPDATE"] = "1" # 保护 agy 补丁不被升级覆盖

    cmd = [exe]
    if not args.no_proxy_server:
        cmd.append("--proxy-server=%s" % url)

    print("IDE      : %s" % exe)
    print("代理     : %s" % url)
    print("环境变量 : HTTP(S)_PROXY / ALL_PROXY / NO_PROXY / NODE_USE_ENV_PROXY=1")
    print("启动参数 : %s" % (" ".join(cmd[1:]) or "(无)"))

    if args.check:
        print("--check: 未启动")
        return 0

    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(cmd, cwd=os.path.dirname(exe), env=env,
                         creationflags=flags, close_fds=True)
    except OSError as e:
        print("启动失败:", e)
        return 1
    print("已启动。等待窗口加载后，在侧边栏点 Sign in with Google。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
