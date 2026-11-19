#!/usr/bin/env python3
"""通过本地 hub RPC 验证登录态与模型目录（不依赖 UI）。

原理：侧边栏面板的数据来自 agy 在本机监听的 HTTP 服务，
端口与 CSRF 令牌写在 %USERPROFILE%\\.gemini\\antigravity\\daemon\\hub_<port>.json。

用法:
    python verify_hub.py
    python verify_hub.py --json

退出码: 0 = 通过（已登录 + 有 hydrogen），1 = 未通过，2 = 找不到 hub
"""
import glob
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")
DAEMON = os.path.join(HOME, ".gemini", "antigravity", "daemon")
SVC = "exa.language_server_pb.LanguageServerService"


def agy_pids():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq agy.exe", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=20).stdout.decode("gbk", "replace")
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


def pick_hub():
    """优先选 pid 属于当前 agy 进程、且最新的 hub 描述文件"""
    files = sorted(glob.glob(os.path.join(DAEMON, "hub_*.json")),
                   key=os.path.getmtime, reverse=True)
    if not files:
        return None, []
    pids = set(agy_pids())
    for f in files:
        try:
            h = json.load(open(f, encoding="utf8"))
        except (OSError, ValueError):
            continue
        if not pids or h.get("pid") in pids:
            return h, files
    # 全部对不上时退回最新的
    try:
        return json.load(open(files[0], encoding="utf8")), files
    except (OSError, ValueError):
        return None, files


def rpc(hub, method):
    url = "http://127.0.0.1:%s/%s/%s" % (hub["httpPort"], SVC, method)
    req = urllib.request.Request(
        url, data=b"{}", method="POST",
        headers={"Content-Type": "application/json",
                 "x-codeium-csrf-token": hub.get("csrfToken", "")})
    raw = urllib.request.urlopen(req, timeout=25).read().decode("utf8")
    return json.loads(raw), raw


def main():
    as_json = "--json" in sys.argv
    hub, files = pick_hub()
    if not hub:
        print("未找到 hub 描述文件（%s）" % DAEMON)
        print("说明 agy 未运行 —— 请先启动 Antigravity IDE。")
        return 2

    result = {"hub": {"port": hub.get("httpPort"), "pid": hub.get("pid"),
                      "csrf": bool(hub.get("csrfToken")),
                      "candidates": len(files)},
              "hasValidAuth": None, "model_count": None,
              "hydrogen": [], "default_model": None, "error": None}

    try:
        auth, _ = rpc(hub, "GetAuthStatus")
        result["hasValidAuth"] = (auth.get("authResult") or {}).get("hasValidAuth")

        models_raw, raw = rpc(hub, "GetAvailableModels")
        resp = models_raw.get("response", models_raw)
        models = resp.get("models", {}) or {}
        result["model_count"] = len(models)
        result["hydrogen"] = sorted(k for k in models if "hydrogen" in k.lower())
        result["default_model"] = resp.get("defaultAgentModelId")
    except urllib.error.URLError as e:
        result["error"] = "连接 hub 失败: %s（端口可能已失效，重启 IDE 后重试）" % e
    except (ValueError, KeyError) as e:
        result["error"] = "响应解析失败: %s" % e

    ok = bool(result["hasValidAuth"]) and bool(result["hydrogen"]) and not result["error"]
    result["verdict"] = "PASS" if ok else "FAIL"

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print("hub 端口      : %s (pid %s, 候选 %d 个)"
          % (result["hub"]["port"], result["hub"]["pid"], result["hub"]["candidates"]))
    print("hasValidAuth  : %s" % result["hasValidAuth"])
    print("模型总数      : %s" % result["model_count"])
    print("默认 Agent 模型: %s" % (result["default_model"] or "-"))
    if result["hydrogen"]:
        print("Hydrogen 模型 :")
        for k in result["hydrogen"]:
            print("  - %s" % k)
    else:
        print("Hydrogen 模型 : 无")
    if result["error"]:
        print("错误          : %s" % result["error"])
    print("-" * 46)
    print("VERDICT: %s" % result["verdict"])
    if not ok and not result["error"]:
        if not result["hasValidAuth"]:
            print("提示: 面板仍未判定合格 —— 确认 agy 已重启加载补丁（fix_agy.py --restart）")
        elif not result["hydrogen"]:
            print("提示: 已登录但模型目录缺少 hydrogen —— 请在 IDE 内 Reload Window 后重试")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
