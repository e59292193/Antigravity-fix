#!/usr/bin/env python3
"""agy 登录资格补丁 —— 版本自适应，可回滚，可重复执行。

背景
----
Antigravity 2.7 起，侧边栏后端 agy.exe 在 OAuth 换码成功后会调用
authclient.ValidateAndOnboardAccount 校验账号。该函数即使拿到
currentTier=standard-tier（付费层）的响应，仍会因 ineligibleTiers 里的
UNSUPPORTED_LOCATION 判定"不合格"，然后丢弃刚换到的 token —— 表现为侧边栏
永远登录不上、报 "this account is ineligible"。

原理
----
该函数成功时会在堆上构造一个结果对象：
    lea  rax, [type]            ; 48 8D 05 <disp32>   7 字节
    call runtime.newobject      ; E8 <rel32>          5 字节
    mov  byte ptr [rax+8], 1    ; C6 40 08 01         4 字节  <- "合格" 标志
把函数入口改写成：
    push rbp; mov rbp,rsp; jmp <lea 处>
即可让任何输入都直接返回合格对象。伪造栈帧是为了让目标块末尾的
leave; ret 能正确收尾（栈平衡 + 16 字节对齐都不破坏）。

用法
----
    python fix_agy.py              # 打补丁（已打过则跳过）
    python fix_agy.py --check      # 只看状态，不写任何文件
    python fix_agy.py --dry-run    # 打印将要写入的字节，不落盘
    python fix_agy.py --restart    # 打完/打完过之后结束 agy，让 IDE 重新拉起
    python fix_agy.py --restore    # 用最近一次备份还原原厂
"""
import argparse
import glob
import hashlib
import json
import os
import re
import stat
import struct
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
AGY = os.path.join(HOME, ".gemini", "bin", "agy.exe")
VER = os.path.join(HOME, ".gemini", "bin", "agy.version.json")

FUNC_EXACT = "authclient.ValidateAndOnboardAccount"
FUNC_FUZZY = "ValidateAndOnboardAccount"

SUCCESS_SIG = b"\xc6\x40\x08\x01"          # mov byte ptr [rax+8], 1
LEA_RAX_RIP = b"\x48\x8d\x05"              # lea rax, [rip+disp32]
CALL_OP = 0xE8

TEXT_VA = 0x1000                            # .text 段 RVA
TEXT_RAW = 0x600                            # .text 段文件偏移
RVA2FILE = TEXT_VA - TEXT_RAW               # 0xA00

PATCH_STUB = b"\x55\x48\x89\xe5\xe9"       # push rbp; mov rbp,rsp; jmp rel32
PATCH_LEN = 9


# --------------------------------------------------------------------------
# PE / pclntab 解析
# --------------------------------------------------------------------------

def find_pclntab(d):
    """定位 Go pclntab。magic 后第 7 字节为指针宽度，8 表示 64 位。"""
    i = d.find(b"\xf1\xff\xff\xff")
    while i != -1:
        if i + 8 <= len(d) and d[i + 7] == 8:
            try:
                nf = struct.unpack_from("<Q", d, i + 8)[0]
                if 1000 < nf < 1000000:
                    return i
            except struct.error:
                pass
        i = d.find(b"\xf1\xff\xff\xff", i + 1)
    raise RuntimeError("未找到 pclntab（不是 Go 二进制，或版本过新）")


def layout(d):
    """pcHeader 字段位置随 Go 版本变化（有无 textStart）。

    两种候选布局都试一次，用"第一个函数名能否解析成合法符号"判定。
    """
    h = find_pclntab(d)
    q = lambda o: struct.unpack_from("<Q", d, o)[0]
    nfunc = q(h + 8)
    for fname_off, pcln_off in ((32, 64), (24, 56)):
        try:
            fn, po = q(h + fname_off), q(h + pcln_off)
            if not (0 < fn < 0x100000 and 0 < po < len(d) - h):
                continue
            base = h + po
            _, f = struct.unpack_from("<II", d, base)
            fo = base + f
            _, no = struct.unpack_from("<Ii", d, fo)
            s = h + fn + no
            nm = d[s:d.index(b"\x00", s)].decode("utf8", "replace")
            if 3 < len(nm) < 200 and ("." in nm or "/" in nm):
                return h, nfunc, fn, po
        except (struct.error, ValueError):
            continue
    raise RuntimeError("pclntab 头部布局识别失败")


def functions(d):
    h, nfunc, fname_off, pcln_off = layout(d)
    base = h + pcln_off
    ents, offs = [], []
    for k in range(nfunc + 1):
        e, f = struct.unpack_from("<II", d, base + k * 8)
        ents.append(e)
        offs.append(f)

    def name(k):
        fo = base + offs[k]
        _, no = struct.unpack_from("<Ii", d, fo)
        s = h + fname_off + no
        return d[s:d.index(b"\x00", s)].decode("utf8", "replace")

    return ents, name


# --------------------------------------------------------------------------
# 定位补丁点
# --------------------------------------------------------------------------

def find_success_block(body, entry_rva):
    """在函数体内找 "合格对象" 构造块，返回 lea 所在 RVA。

    严格要求：C6 40 08 01 前 12 字节为 lea rax,[rip+x] (7) + call (5)。
    只认这个形态，避免误命中其它结构体赋值。
    """
    start = 0
    while True:
        pos = body.find(SUCCESS_SIG, start)
        if pos < 0:
            break
        start = pos + 1
        if pos < 12:
            continue
        lea_at = pos - 12
        if body[lea_at:lea_at + 3] != LEA_RAX_RIP:
            continue
        if body[pos - 5] != CALL_OP:
            continue
        return entry_rva + lea_at
    return None


def locate(d):
    """返回 (entry_rva, success_rva, func_size, func_name)"""
    ents, name = functions(d)
    exact, fuzzy = [], []
    for k in range(len(ents) - 1):
        nm = name(k)
        if nm.endswith(FUNC_EXACT):
            exact.append(k)
        elif FUNC_FUZZY in nm and "deferwrap" not in nm:
            fuzzy.append(k)
    cands = exact or fuzzy
    if not cands:
        raise RuntimeError(
            "未找到 %s —— agy 版本结构可能已变，请按 docs/reverse-engineering.md 手动定位"
            % FUNC_EXACT)

    errors = []
    for k in cands:
        entry = ents[k] + TEXT_VA
        size = ents[k + 1] - ents[k]
        off = entry - RVA2FILE
        body = d[off:off + size]
        success = find_success_block(body, entry)
        if success is None:
            errors.append("%s (size 0x%x): 未找到合格块特征" % (name(k), size))
            continue
        return entry, success, size, name(k)
    raise RuntimeError("；".join(errors) or "定位失败")


# --------------------------------------------------------------------------
# 文件操作
# --------------------------------------------------------------------------

def is_readonly(path):
    try:
        return bool(os.stat(path).st_file_attributes & stat.FILE_ATTRIBUTE_READONLY)
    except (AttributeError, OSError):
        return False


def set_readonly(path, on):
    try:
        os.chmod(path, stat.S_IREAD if on else stat.S_IWRITE)
    except OSError:
        pass


def sha8(b):
    return hashlib.sha256(b).hexdigest()[:8]


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


def cleanup_stale(path):
    """清理历史遗留的 .old-* / .running-*（仅当已不被占用）"""
    for pat in (path + ".old-*", path + ".running-*", path + ".new"):
        for f in glob.glob(pat):
            try:
                os.remove(f)
            except OSError:
                pass


def find_matching_backup(path, data):
    """已有备份内容相同就直接复用，避免重复占用 200MB+ 磁盘"""
    target = hashlib.sha256(data).hexdigest()
    for f in glob.glob(path + ".*bak"):
        try:
            if os.path.getsize(f) != len(data):
                continue
            if hashlib.sha256(open(f, "rb").read()).hexdigest() == target:
                return f
        except OSError:
            continue
    return None


def write_patch_inplace(path, off, patch):
    set_readonly(path, False)
    with open(path, "r+b") as f:
        f.seek(off)
        f.write(patch)
        f.flush()
        os.fsync(f.fileno())


def write_patch_swap(path, patched_bytes):
    """文件被运行中的 agy 占用时：写副本 -> 原文件改名 -> 副本就位。

    Windows 允许重命名正在运行的 exe，运行中的进程继续用旧句柄，不受影响。
    """
    tmp = path + ".new"
    with open(tmp, "wb") as f:
        f.write(patched_bytes)
        f.flush()
        os.fsync(f.fileno())
    old = path + ".old-%d" % int(time.time())
    os.replace(path, old)
    os.replace(tmp, path)
    return old


def sync_version_json(path):
    # 只对"真正的安装位置"同步元信息；对副本操作时不动全局版本记录
    if os.path.abspath(path) != os.path.abspath(AGY):
        return
    if not os.path.isfile(VER):
        return
    try:
        info = json.load(open(VER, encoding="utf8"))
    except (OSError, ValueError):
        return
    d = open(path, "rb").read()
    info["size"] = len(d)
    info["sha512"] = hashlib.sha512(d).hexdigest()
    info["mtimeMs"] = int(os.stat(path).st_mtime * 1000)
    json.dump(info, open(VER, "w", encoding="utf8"), indent=2)


def restart_agy(wait=20):
    old = set(agy_pids())
    if not old:
        print("agy 当前未运行，无需重启")
        return True
    for pid in old:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=20)
    print("已结束 agy:", sorted(old), "等待 IDE 重新拉起 ...")
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(1)
        now = set(agy_pids())
        if now - old:
            print("agy 已重生:", sorted(now))
            return True
    print("注意：%d 秒内 agy 未重生，请手动启动 Antigravity IDE" % wait)
    return False


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="为 agy.exe 打登录资格补丁（版本自适应）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="只检查状态")
    ap.add_argument("--dry-run", action="store_true", help="打印补丁字节但不写入")
    ap.add_argument("--restore", action="store_true", help="从备份还原原厂文件")
    ap.add_argument("--restart", action="store_true", help="结束后重启 agy 使其加载新二进制")
    ap.add_argument("--agy", default=AGY, help="agy.exe 路径（默认 ~/.gemini/bin/agy.exe）")
    args = ap.parse_args()

    path = os.path.expanduser(args.agy)
    if not os.path.isfile(path):
        print("找不到 agy.exe:", path)
        print("请先启动一次 Antigravity IDE 并点一次登录，让 IDE 自动下载 agy。")
        return 2

    if args.restore:
        baks = sorted(glob.glob(path + ".*bak"), key=os.path.getmtime, reverse=True)
        if not baks:
            print("没有可用备份")
            return 1
        bak = baks[0]
        set_readonly(path, False)
        open(path, "wb").write(open(bak, "rb").read())
        print("已还原:", os.path.basename(bak), "->", os.path.basename(path))
        print("提示：还原后登录会重新失败；如需再次修复请不加 --restore 重跑")
        return 0

    cleanup_stale(path)
    d = open(path, "rb").read()

    try:
        entry, success, size, fname = locate(d)
    except RuntimeError as e:
        print("定位失败:", e)
        return 1

    off = entry - RVA2FILE
    cur = d[off:off + PATCH_LEN]
    patched = cur[:4] == PATCH_STUB[:4] and cur[4] == PATCH_STUB[4]

    print("目标函数 : %s" % fname)
    print("函数入口 : RVA 0x%x  file 0x%x  size 0x%x" % (entry, off, size))
    print("合格块   : RVA 0x%x" % success)
    print("入口字节 : %s" % cur.hex())
    print("只读属性 : %s" % is_readonly(path))
    print("当前状态 : %s" % ("已打补丁" if patched else "未打补丁（原厂）"))

    if patched:
        if args.restart:
            restart_agy()
        return 0

    rel = success - (entry + PATCH_LEN)
    if not (-0x80000000 <= rel < 0x80000000):
        print("跳转距离超出 rel32 范围，拒绝打补丁")
        return 1
    patch = PATCH_STUB + rel.to_bytes(4, "little", signed=True)
    print("将写入   : %s  (rel=0x%x)" % (patch.hex(), rel & 0xFFFFFFFF))

    if args.check:
        return 1
    if args.dry_run:
        print("dry-run：未写入任何字节")
        return 0

    # 备份原厂文件（已存在同内容备份则复用）
    bak = find_matching_backup(path, d)
    if bak:
        print("已备份   : %s（复用已有备份）" % os.path.basename(bak))
    else:
        bak = path + ".%sbak" % sha8(d)
        with open(bak, "wb") as f:
            f.write(d)
            f.flush()
            os.fsync(f.fileno())
        print("已备份   :", os.path.basename(bak))

    mode = "inplace"
    try:
        write_patch_inplace(path, off, patch)
    except (PermissionError, OSError) as e:
        print("文件被占用（%s），改用改名换文件" % type(e).__name__)
        buf = bytearray(d)
        buf[off:off + PATCH_LEN] = patch
        old = write_patch_swap(path, bytes(buf))
        mode = "swap"
        print("原文件已改名为:", os.path.basename(old))

    # 回读校验
    nd = open(path, "rb").read()
    if nd[off:off + PATCH_LEN] != patch:
        print("写入校验失败！当前字节:", nd[off:off + PATCH_LEN].hex())
        return 1

    sync_version_json(path)
    set_readonly(path, True)

    print("完成     : 补丁已写入（%s）并设为只读" % mode)
    if mode == "swap":
        print("提示     : 旧文件 agy.exe.old-* 仍在被进程占用，下次运行本脚本会自动清理")
    if args.restart:
        restart_agy()
    else:
        print("下一步   : python fix_agy.py --restart   （让 agy 重新加载）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
