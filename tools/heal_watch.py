#!/usr/bin/env python3
"""agy 补丁守护 —— 拉起 IDE 后在后台盯一段时间，抓到新版本替换就自动重打。

为什么需要"盯一段时间"
----------------------
agy 的自动升级发生在它启动后的一小会儿（要下载 ~235MB），
所以"启动前检查一次"经常太早，补丁会被随后落地的升级覆盖。
本脚本按固定间隔重复自愈，覆盖升级落地的窗口。

用法（一般由启动脚本以 pythonw 后台拉起）
    pythonw heal_watch.py                  # 10 轮 × 30 秒 ≈ 5 分钟
    pythonw heal_watch.py --rounds 20 --interval 60
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HEAL = os.path.join(HERE, "heal_agy.py")
DEFAULT_ROUNDS = 10
DEFAULT_INTERVAL = 30


def python_exe():
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        alt = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(alt):
            return alt
    return exe


def arg(name, default):
    argv = sys.argv
    if name in argv:
        try:
            return int(argv[argv.index(name) + 1])
        except (IndexError, ValueError):
            return default
    return default


def main():
    rounds = arg("--rounds", DEFAULT_ROUNDS)
    interval = arg("--interval", DEFAULT_INTERVAL)
    py = python_exe()
    flags = 0x08000000  # CREATE_NO_WINDOW
    for _ in range(max(1, rounds)):
        try:
            subprocess.run([py, HEAL], creationflags=flags,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=600)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(max(5, interval))
    return 0


if __name__ == "__main__":
    sys.exit(main())
