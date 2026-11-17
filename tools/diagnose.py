#!/usr/bin/env python3
"""一键诊断 Antigravity 登录环境，输出结论码。

只读，不修改任何文件。

用法:
    python diagnose.py            # 人类可读报告
    python diagnose.py --json     # 机器可读（给 Agent 解析）
    python diagnose.py --verbose  # 附带日志原文片段

结论码（最后一行 / JSON 的 verdict 字段）:
    OK               补丁在位、登录态有效、网络可用
    NO_AGY           agy.exe 不存在（需先启动一次 IDE 让它下载）
    PATCH_MISSING    agy 是原厂状态，补丁未打
    PATCH_STALE      补丁在，但 version.json 与实际文件不一致
    AUTOUPDATE_ON    补丁在，但自动更新未关，随时会被覆盖
    LOG_INELIGIBLE   日志显示最近一次登录仍被判 ineligible
    LOG_TIMEOUT      日志出现连接超时（代理未注入）
    PROXY_DOWN       本机未发现可用代理端口
"""
import glob
import json
import os
import re
import socket
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

HOME = os.path.expanduser("~")
AGY = os.path.join(HOME, ".gemini", "bin", "agy.exe")
VER = os.path.join(HOME, ".gemini", "bin", "agy.version.json")
CLI_LOG = os.path.join(HOME, ".gemini", "antigravity", "cli.log")

PROXY_CANDIDATES = [7892, 7890, 7897, 1080, 8889, 2080, 8080, 10808]
IDE_CANDIDATES = [
    os.path.join(HOME, "AppData", "Local", "Programs", "Antigravity IDE", "Antigravity IDE.exe"),
    os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Antigravity IDE", "Antigravity IDE.exe"),
    r"D:\APP\Antigravity IDE新版\Antigravity IDE\Antigravity IDE.exe",
]


# --------------------------------------------------------------------------

def port_open(port, timeout=0.35):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def detect_proxy():
    open_ports = [p for p in PROXY_CANDIDATES if port_open(p)]
    env_proxy = None
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        v = os.environ.get(k)
        if v:
            env_proxy = v
            break
    return open_ports, env_proxy


def agy_pids():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq agy.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = [p.strip().strip('"') for p in line.split('","')]
        if parts and parts[0].lower() == "agy.exe" and len(parts) > 1:
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def ide_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Antigravity IDE.exe", "/NH"],
                             capture_output=True, text=True, timeout=20).stdout
        return "Antigravity IDE.exe" in out
    except Exception:
        return False


def read_version_json():
    if not os.path.isfile(VER):
        return None
    try:
        return json.load(open(VER, encoding="utf8"))
    except (OSError, ValueError):
        return None


def env_persisted(name):
    """读用户级环境变量（不依赖当前进程是否继承）。

    用 winreg 而不是 reg.exe：后者在部分受限环境里会被安全策略拦截。
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            v, _ = winreg.QueryValueEx(k, name)
            return str(v).strip() not in ("", "0")
    except (ImportError, OSError):
        return False


def analyze_log():
    """返回日志里的关键信号（只看最后 400 行）"""
    info = {
        "exists": os.path.isfile(CLI_LOG),
        "last_failure_kind": None,
        "last_has_token": None,
        "last_auth_status": None,
        "cn_location": False,
        "timeout": False,
        "tail": [],
    }
    if not info["exists"]:
        return info
    try:
        with open(CLI_LOG, "r", encoding="utf8", errors="replace") as f:
            lines = f.readlines()[-400:]
    except OSError:
        return info
    info["tail"] = lines[-8:]
    for line in lines:
        for m in re.finditer(r"failure_kind=(\w+)", line):
            info["last_failure_kind"] = m.group(1)
        for m in re.finditer(r"has_token=(\w+)", line):
            info["last_has_token"] = m.group(1)
        for m in re.finditer(r"get_auth_status valid=(\w+)", line):
            info["last_auth_status"] = m.group(1)
        if "Detected location: CN" in line:
            info["cn_location"] = True
        if re.search(r"connect timeout|UND_ERR_CONNECT_TIMEOUT|i/o timeout", line):
            info["timeout"] = True
    return info


def check_patch():
    """复用 fix_agy 的定位逻辑，返回 dict"""
    res = {"state": "unknown", "detail": "", "entry": None, "success": None,
           "func": None, "size": None}
    if not os.path.isfile(AGY):
        res["state"] = "no_file"
        return res
    try:
        import fix_agy as F
    except Exception as e:
        res["detail"] = "无法导入 fix_agy: %s" % e
        return res
    d = open(AGY, "rb").read()
    res["file_size"] = len(d)
    try:
        entry, success, size, fname = F.locate(d)
    except RuntimeError as e:
        res["detail"] = str(e)
        return res
    off = entry - F.RVA2FILE
    cur = d[off:off + 9]
    res.update(entry=hex(entry), success=hex(success), func=fname, func_size=size,
               entry_bytes=cur.hex(),
               patched=(cur[:4] == F.PATCH_STUB[:4] and cur[4] == F.PATCH_STUB[4]))
    res["state"] = "patched" if res["patched"] else "pristine"
    res["readonly"] = bool(os.stat(AGY).st_file_attributes & 1) if hasattr(os.stat(AGY), "st_file_attributes") else None
    return res


def main():
    jout = "--json" in sys.argv
    verbose = "--verbose" in sys.argv

    agy_exists = os.path.isfile(AGY)
    ver = read_version_json()
    patch = check_patch()
    pids = agy_pids()
    proxy_ports, env_proxy = detect_proxy()
    log = analyze_log()
    auto_off = env_persisted("AGY_CLI_DISABLE_AUTO_UPDATE") or \
        os.environ.get("AGY_CLI_DISABLE_AUTO_UPDATE") == "1"
    ide_exe = next((p for p in IDE_CANDIDATES if os.path.isfile(p)), None)

    # ---- version.json 一致性 ----
    stale = False
    if agy_exists and ver and os.path.isfile(AGY):
        import hashlib
        d = open(AGY, "rb").read()
        if ver.get("size") and ver["size"] != len(d):
            stale = True
        if ver.get("sha512") and ver["sha512"] != hashlib.sha512(d).hexdigest():
            stale = True

    # ---- 结论判定 ----
    if not agy_exists:
        verdict = "NO_AGY"
    elif patch.get("state") == "pristine":
        verdict = "PATCH_MISSING"
    elif log.get("last_failure_kind") == "ineligible":
        verdict = "LOG_INELIGIBLE"
    elif stale:
        verdict = "PATCH_STALE"
    elif not auto_off:
        verdict = "AUTOUPDATE_ON"
    elif log.get("timeout") and not proxy_ports:
        verdict = "PROXY_DOWN"
    elif log.get("timeout"):
        verdict = "LOG_TIMEOUT"
    elif not proxy_ports:
        verdict = "PROXY_DOWN"
    else:
        verdict = "OK"

    advice = {
        "OK": "环境正常，无需修复。若 UI 仍不显示模型，请 Reload Window。",
        "NO_AGY": "先启动一次 IDE 并点一次登录，让 agy.exe 自动下载，然后重跑本诊断。",
        "PATCH_MISSING": "运行 python tools/fix_agy.py 打补丁，然后 --restart。",
        "PATCH_STALE": "运行 python tools/fix_agy.py（会自动同步 version.json 的 size/sha512）。",
        "AUTOUPDATE_ON": "运行 setx AGY_CLI_DISABLE_AUTO_UPDATE 1，并对 agy.exe 设只读。",
        "LOG_INELIGIBLE": "补丁已在位但登录仍被判不合格：确认 agy 已重启加载新二进制；"
                          "若仍失败，按 docs/reverse-engineering.md 重新定位。",
        "LOG_TIMEOUT": "代理端口在但请求超时：用 python tools/launch_ide.py 启动 IDE（注入代理变量）。",
        "PROXY_DOWN": "未发现可用代理端口。请先启动代理软件，或用 --proxy 指定端口。",
    }[verdict]

    if jout:
        print(json.dumps({
            "verdict": verdict,
            "advice": advice,
            "agy": {"path": AGY, "exists": agy_exists,
                    "version": (ver or {}).get("version"),
                    "size": patch.get("file_size"), "pids": pids},
            "patch": patch,
            "version_json_stale": stale,
            "autoupdate_disabled": auto_off,
            "proxy": {"open_ports": proxy_ports, "env": env_proxy},
            "log": {k: v for k, v in log.items() if k != "tail"},
            "ide": {"exe": ide_exe, "running": ide_running()},
        }, ensure_ascii=False, indent=2))
        return

    print("=" * 62)
    print(" Antigravity 登录环境诊断")
    print("=" * 62)
    print("OS            : %s %s" % (sys.platform, struct.calcsize("P") * 8))
    print("Python        : %s" % sys.version.split()[0])
    print("agy.exe       : %s" % (AGY if agy_exists else "缺失"))
    print("agy 版本      : %s" % ((ver or {}).get("version") or "未知"))
    print("agy 文件大小  : %s" % (("%d" % patch["file_size"]) if patch.get("file_size") else "-"))
    print("agy 进程      : %s" % (pids or "未运行"))
    print("IDE 安装      : %s" % (ide_exe or "未找到（可用 --proxy 手动指定）"))
    print("IDE 运行      : %s" % ("是" if ide_running() else "否"))
    print("-" * 62)
    print("目标函数      : %s" % (patch.get("func") or "-"))
    print("入口 RVA      : %s" % (patch.get("entry") or "-"))
    print("合格块 RVA    : %s" % (patch.get("success") or "-"))
    print("入口字节      : %s" % (patch.get("entry_bytes") or "-"))
    print("补丁状态      : %s" % {"patched": "已打补丁", "pristine": "原厂（未打）",
                                  "no_file": "无文件", "unknown": "无法判定"}
          .get(patch.get("state"), patch.get("state")))
    if patch.get("detail"):
        print("定位详情      : %s" % patch["detail"])
    print("只读保护      : %s" % patch.get("readonly"))
    print("version.json  : %s" % ("与实际文件不一致（需同步）" if stale else "一致"))
    print("自动更新开关  : %s" % ("已关闭" if auto_off else "未关闭（有被覆盖风险）"))
    print("-" * 62)
    print("代理端口      : %s" % (proxy_ports or "无"))
    print("环境变量代理  : %s" % (env_proxy or "未设置"))
    print("-" * 62)
    print("日志          : %s" % (CLI_LOG if log["exists"] else "不存在"))
    print("最近登录结果  : failure_kind=%s" % (log["last_failure_kind"] or "-"))
    print("Token 落盘    : has_token=%s" % (log["last_has_token"] or "-"))
    print("面板状态      : valid=%s" % (log["last_auth_status"] or "-"))
    print("地区判定 CN   : %s（aicode 域名，非阻塞）" % ("是" if log["cn_location"] else "否"))
    print("连接超时痕迹  : %s" % ("有" if log["timeout"] else "无"))
    if verbose and log["tail"]:
        print("-" * 62)
        print("日志尾部:")
        for line in log["tail"]:
            print("  " + line.rstrip()[:160])
    print("=" * 62)
    print("建议: %s" % advice)
    print()
    print("VERDICT: %s" % verdict)


if __name__ == "__main__":
    main()
