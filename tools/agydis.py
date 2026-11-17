#!/usr/bin/env python3
"""agy.exe 反汇编辅助（仅在需要为**新版本**重新定位补丁点时才用到）。

fix_agy.py 本身不依赖 capstone；本脚本需要：
    pip install capstone

用法:
    python agydis.py 0x2299c80 0x600              # 反汇编 RVA 区间
    python agydis.py --func ValidateAndOnboard    # 按名字模糊查找并反汇编整函数
    python agydis.py --list "authclient\\."        # 列出匹配的函数（名 + RVA + size）
    python agydis.py --callers ValidateAndOnboard # 找谁调用了它
    python agydis.py --search c6400801            # 搜机器码（十六进制）
"""
import argparse
import bisect
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64
except ImportError:
    print("缺少 capstone：pip install capstone")
    sys.exit(2)

import fix_agy as F

AGY = F.AGY


class Binary:
    def __init__(self, path=None):
        self.path = path or AGY
        self.d = open(self.path, "rb").read()
        self.ents, self._name = F.functions(self.d)

    def name(self, k):
        return self._name(k)

    def find(self, pattern, fuzzy=True):
        rx = re.compile(pattern)
        out = []
        for k in range(len(self.ents) - 1):
            nm = self.name(k)
            if (rx.search(nm) if fuzzy else nm == pattern):
                out.append(k)
        return out

    def index_at(self, rva):
        e = rva - F.TEXT_VA
        k = bisect.bisect_right(self.ents, e) - 1
        if 0 <= k < len(self.ents) - 1 and self.ents[k] <= e < self.ents[k + 1]:
            return k
        return None

    def name_at(self, rva):
        k = self.index_at(rva)
        return self.name(k) if k is not None else None

    def short(self, rva):
        nm = self.name_at(rva)
        if not nm:
            return None
        return nm.split(".")[-1] if "." in nm else nm


def disasm(b, start_rva, size, annotate=True):
    off = start_rva - F.RVA2FILE
    code = b.d[off:off + size]
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    for ins in md.disasm(code, start_rva):
        note = ""
        if annotate:
            if ins.mnemonic in ("call", "jmp") and ins.op_str.startswith("0x"):
                n = b.short(int(ins.op_str, 16))
                if n:
                    note = "   ; %s" % n
            elif ins.mnemonic.startswith("j") and ins.op_str.startswith("0x"):
                n = b.short(int(ins.op_str, 16))
                if n:
                    note = "   ; -> %s" % n
        print("%08x  %-24s %-7s %s%s"
              % (ins.address, ins.bytes.hex(), ins.mnemonic, ins.op_str, note))


def main():
    ap = argparse.ArgumentParser(description="agy.exe 反汇编辅助")
    ap.add_argument("rva", nargs="?", help="起始 RVA（十六进制，可带 0x）")
    ap.add_argument("size", nargs="?", default="0x200", help="长度（十六进制）")
    ap.add_argument("--func", help="模糊匹配函数名并反汇编整个函数")
    ap.add_argument("--list", help="列出匹配的函数名（正则）")
    ap.add_argument("--callers", help="查找调用指定函数的调用点")
    ap.add_argument("--search", help="搜索机器码（十六进制，不含空格）")
    ap.add_argument("--agy", help="agy.exe 路径")
    args = ap.parse_args()

    b = Binary(args.agy)

    if args.list:
        ks = b.find(args.list)
        print("匹配 %d 个函数（显示前 40）:" % len(ks))
        for k in ks[:40]:
            print("  RVA 0x%08x  size 0x%-5x  %s"
                  % (b.ents[k] + F.TEXT_VA, b.ents[k + 1] - b.ents[k], b.name(k)))
        return 0

    if args.func:
        ks = b.find(args.func)
        if not ks:
            print("未找到匹配 %s 的函数" % args.func)
            return 1
        for k in ks[:5]:
            rva = b.ents[k] + F.TEXT_VA
            size = b.ents[k + 1] - b.ents[k]
            print("=== %s  RVA 0x%x  size 0x%x ===" % (b.name(k), rva, size))
            disasm(b, rva, size)
            print()
        return 0

    if args.callers:
        ks = b.find(args.callers)
        if not ks:
            print("未找到目标函数")
            return 1
        targets = {b.ents[k] + F.TEXT_VA for k in ks}
        print("目标:", ", ".join(hex(t) for t in targets))
        seen = set()
        for off, byte in enumerate(b.d):
            if byte != 0xE8 or off + 5 > len(b.d):
                continue
            rel = struct.unpack_from("<i", b.d, off + 1)[0]
            rva = off + F.RVA2FILE
            if rva + 5 + rel in targets:
                key = (rva,)
                if key in seen:
                    continue
                seen.add(key)
                print("  call @ RVA 0x%x file 0x%x  在 %s"
                      % (rva, off, b.name_at(rva) or "?"))
        return 0

    if args.search:
        pat = bytes.fromhex(args.search.replace(" ", ""))
        start = 0
        hits = 0
        while hits < 40:
            i = b.d.find(pat, start)
            if i < 0:
                break
            rva = i + F.RVA2FILE
            print("  file 0x%-9x RVA 0x%-9x  %s" % (i, rva, b.name_at(rva) or "?"))
            start = i + 1
            hits += 1
        if not hits:
            print("未命中")
        return 0

    if not args.rva:
        ap.print_help()
        return 1
    disasm(b, int(args.rva, 16), int(args.size, 16))
    return 0


if __name__ == "__main__":
    sys.exit(main())
