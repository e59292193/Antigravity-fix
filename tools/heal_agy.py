#!/usr/bin/env python3
"""agy 登录补丁自愈 —— 拉起 IDE / 开机后，补丁被新版本覆盖就自动重打。

背景
----
agy.exe 会自我升级（1.5.9 -> 1.5.10 -> 1.5.12 都是整文件替换），
升级后 authclient.ValidateAndOnboardAccount 的入口补丁消失，
侧边栏随即报 "this account is not eligible ... location"。

做法
----
1. 用 (size, mtime) 缓存判断，文件没变时几乎零开销退出
2. 补丁缺失 -> 调用 fix_agy.py 重打（版本自适应）-> 恢复只读属性
3. 只有"刚刚打过补丁"才结束 agy 进程，让 IDE 用新二进制重生

注意（踩过的坑）
----------------
不要用 ACL 拒绝项（icacls /deny）去保护这个文件：
icacls 生成的拒绝 ACE 会连带 SYNCHRONIZE，导致文件连读都读不了，
且拒绝项优先级高于"所有者隐含权限"，事后连 ACL 都改不回来。
只读属性 + 本脚本轮询已经够用。

用法
----
    python heal_agy.py            # 自愈（默认）
    python heal_agy.py --status   # 只看状态
    python heal_agy.py --no-restart
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fix_agy.py")
AGY = os.path.join(os.path.expanduser("~"), ".gemini", "bin", "agy.exe")
STATE = os.path.join(HERE, "heal_state.json")
LOG = os.path.join(HERE, "heal.log")


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        if os.path.isfile(LOG) and os.path.getsize(LOG) > 1024 * 1024:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a", encoding="utf8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _env():
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONUTF8"] = "1"
    return e


def _python():
    """本脚本由 pythonw 启动时，子进程改用同目录 python.exe 才有 stdout"""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        alt = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(alt):
            return alt
    return exe


def run(cmd, timeout=300):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           env=_env(), creationflags=0x08000000)  # CREATE_NO_WINDOW
        raw = p.stdout or b""
        for enc in ("utf-8", "gbk"):
            try:
                return p.returncode, raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return p.returncode, raw.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def agy_pids():
    rc, out = run(["tasklist", "/FI", "IMAGENAME eq agy.exe", "/FO", "CSV", "/NH"], 30)
    pids = []
    for line in out.splitlines():
        parts = [x.strip().strip('"') for x in line.split('","')]
        if parts and parts[0].lower() == "agy.exe" and len(parts) > 1:
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def check_patched():
    rc, out = run([_python(), FIX, "--check"], 300)
    return rc == 0, out.strip()


def read_state():
    try:
        return json.load(open(STATE, encoding="utf8"))
    except (OSError, ValueError):
        return {}


def write_state(d):
    try:
        json.dump(d, open(STATE, "w", encoding="utf8"), indent=2)
    except OSError:
        pass


def restart_agy():
    old = set(agy_pids())
    if not old:
        return True
    for pid in old:
        run(["taskkill", "/F", "/PID", str(pid)], 30)
    deadline = time.time() + 25
    while time.time() < deadline:
        time.sleep(1)
        if set(agy_pids()) - old:
            return True
    return False


def main():
    status_only = "--status" in sys.argv
    no_restart = "--no-restart" in sys.argv

    if not os.path.isfile(AGY):
        print("找不到 agy.exe:", AGY)
        log("SKIP 找不到 agy.exe")
        return 0

    st = os.stat(AGY)
    key = [st.st_size, st.st_mtime_ns]
    cache = read_state()

    if cache.get("key") == key and cache.get("patched"):
        if status_only:
            print("agy.exe   :", AGY)
            print("大小/时间 :", st.st_size,
                  time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)))
            print("补丁      : 已打（缓存命中）")
            print("只读      :", bool(st.st_file_attributes & 0x1))
        return 0

    patched, detail = check_patched()

    if status_only:
        print("agy.exe   :", AGY)
        print("大小/时间 :", st.st_size,
              time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)))
        print("补丁      :", "已打" if patched else "未打")
        print("只读      :", bool(st.st_file_attributes & 0x1))
        print(detail)
        return 0

    if patched:
        write_state({"key": key, "patched": True, "at": time.time()})
        return 0

    log("HEAL 检测到补丁缺失（%d 字节 / %s），开始重打"
        % (st.st_size, time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime))))
    rc, out = run([_python(), FIX], 600)
    ok = rc == 0 and "补丁已写入" in out
    if ok:
        log("HEAL 成功 :: " + " | ".join(
            l.strip() for l in out.splitlines()
            if "入口" in l or "合格块" in l or "完成" in l))
        if not no_restart and agy_pids():
            log("RESTART " + ("agy 已重生" if restart_agy() else "agy 未重生，请重开 IDE 窗口"))
    else:
        log("HEAL 失败 rc=%s :: %s" % (rc, (out or detail)[-400:]))

    st = os.stat(AGY)
    write_state({"key": [st.st_size, st.st_mtime_ns], "patched": ok, "at": time.time()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
