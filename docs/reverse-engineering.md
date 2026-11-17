# 在新版本 agy 中重新定位补丁点

`fix_agy.py` 正常情况下不需要你看这篇。只有当它报
**"未找到 authclient.ValidateAndOnboardAccount"** 或
**"未找到合格块特征"** 时，才需要按本文手动定位。

---

## 一、为什么不能直接搜字符串或字节

agy 是 Go 编译、已 strip 的 x64 PE：

- `go tool nm` / `objdump` 拿不到符号
- `C6 40 08 01`（`mov byte[rax+8],1`）这种模式在二进制里出现几十次，属于不同结构体
- 文件偏移 ≠ RVA，差值是固定的但容易被忽略

唯一可靠的符号来源是 Go 自己嵌入的 **`pclntab`**（`gopclntab`）。

---

## 二、两个必须记住的换算

### 2.1 PE 节映射

```
.text 段：VirtualAddress = 0x1000，PointerToRawData = 0x600
=> RVA = file_offset + 0xA00
=> file_offset = RVA - 0xA00
```

这个 `0xA00` 是 agy 特有的，换程序要重新读节表。

### 2.2 pclntab 里的函数偏移

```
函数真实 RVA = entryoff + 0x1000     # 同样是 .text 的 VA
```

`entryoff` 是相对 `.text` 段起点的偏移，不是 RVA。

---

## 三、pclntab 结构

### 3.1 定位 magic

magic 是 `F1 FF FF FF`（Go 1.20+ 的 `0xFFFFFFF1`）。
后面紧跟 `00 00`（pad）、`minLC`、`ptrSize`。

```python
i = d.find(b"\xf1\xff\xff\xff")
while i != -1:
    if d[i + 7] == 8:            # ptrSize == 8 表示 64 位
        ...
    i = d.find(b"\xf1\xff\xff\xff", i + 1)
```

**不能只取第一个命中**：`.rdata` 里可能有假阳性。用 `ptrSize==8` +
`nfunc` 落在合理区间（1000 ~ 1000000）双重过滤。

### 3.2 头部字段位置会变

```
偏移  字段
+8    nfunc
+16   nfiles
+24   funcnameOffset     ← 布局 A
+32   funcnameOffset     ← 布局 B（多了 textStart 字段）
+56   pclnOffset         ← 布局 A
+64   pclnOffset         ← 布局 B
```

判别方法（`fix_agy.py` 的 `layout()`）：两种布局都试，读 `functab[0]` 的
`funcoff`，跳到函数名，看能否解析出含 `.` 或 `/` 的合法符号。能解析的就是对的。

实测：

| agy 版本 | 布局 |
|---|---|
| 1.5.9 | A（+24 / +56） |
| 1.5.10 | B（+32 / +64） |

### 3.3 functab

```
base = pclntab + pclnOffset
每一项 8 字节：<uint32 entryoff, uint32 funcoff>
共 nfunc+1 项（最后一项是 sentinel，给出末函数结束地址）
```

函数名结构（在 `base + funcoff`）：

```
<int32 entryOff, int32 nameOff>
name 在 pclntab + funcnameOffset + nameOff 处，NUL 结尾
```

---

## 四、定位目标函数

```python
for k in range(nfunc):
    if name(k).endswith("authclient.ValidateAndOnboardAccount"):
        entry = ents[k] + 0x1000
        size  = ents[k+1] - ents[k]
```

实测全名：

```
google3/third_party/jetski/language_server/auth_client/authclient.ValidateAndOnboardAccount
```

**注意**：名字前缀可能随代码组织变化。用 `endswith` 匹配，不要用全等。
如果 `endswith` 找不到，退化为 `"ValidateAndOnboardAccount" in name`
（可能命中 `(*ServerBackend).ValidateAndOnboardAccount` 等包装函数，
此时要看函数体里有没有合格块特征，没有就换下一个候选）。

---

## 五、定位"合格块"

### 5.1 特征

```asm
lea  rax, [rip+disp32]     ; 48 8D 05 xx xx xx xx     7 字节
call runtime.newobject     ; E8 xx xx xx xx           5 字节
mov  byte ptr [rax+8], 1   ; C6 40 08 01              4 字节
```

这 16 字节是"分配结果对象并把 eligible 置 true"的固定形态。

### 5.2 为什么要校验前 12 字节

只搜 `C6 40 08 01` 会误命中大量无关结构体赋值（实测全文件几十处）。
加上"前 12 字节必须是 `48 8D 05` + `E8`"这个约束后，函数内唯一命中。

```python
pos = body.find(b"\xc6\x40\x08\x01")
lea_at = pos - 12
assert body[lea_at:lea_at+3] == b"\x48\x8d\x05"
assert body[pos-5] == 0xE8
success = entry + lea_at
```

### 5.3 不要跳到 `mov` 本身

必须跳到 `lea` 处。直接跳到 `mov byte[rax+8],1` 会因为 `rax` 未初始化而崩栈
（`newobject` 没被调用）。这是最容易犯的错。

---

## 六、构造补丁

```
rel = success - (entry + 9)
patch = 55 48 89 E5 E9 <rel32 小端有符号>
```

即 `push rbp; mov rbp,rsp; jmp success`。

为什么是 `push rbp; mov rbp,rsp` 而不是直接 `jmp`：

- 目标块末尾是 `leave; ret`
- `leave` 等价于 `mov rsp,rbp; pop rbp`
- 如果没有 `mov rbp,rsp`，`rsp` 会被恢复成调用者的值 → 返回地址错位 → 崩溃

`jmp` 不压栈，所以 16 字节对齐保持不变（`call` 会破坏对齐，不要用 `call`）。

---

## 七、验证补丁

### 7.1 静态

```bash
python tools/fix_agy.py --dry-run      # 打印入口/合格块/将写入的字节
```

对比手工反汇编的结果，三处必须一致：函数名、入口 RVA、合格块 RVA。

### 7.2 动态

```bash
python tools/fix_agy.py --restart
tail -3 ~/.gemini/antigravity/cli.log          # 应有 initialized server successfully
ls -la ~/.gemini/antigravity/crashes/          # 不应有新增非空文件
python tools/verify_hub.py                     # 应 PASS
```

如果 agy 起不来（进程秒退），说明跳转目标错了：

1. 回滚：`python tools/fix_agy.py --restore`
2. 用 `python tools/agydis.py --func ValidateAndOnboard` 重新看函数尾部
3. 确认 `leave; ret` 的位置，把跳转目标改到它前面的对象构造处

---

## 八、血泪教训：如何确认补丁点"会被执行"

第一版补丁打在 `(*ServerBackend).handleAuthResult.func1` 的某个分支上，
位置"看起来"完全正确（跳转目标是合格分支），但**从来没生效**。

原因：该分支之前执行了

```asm
xor eax, eax
xor ecx, ecx
xor edx, edx
test rax, rax
test rcx, rcx
```

寄存器已被清零，测试条件恒定，后面那条 `cmp byte[rcx+8],0` **永远执行不到** ——
是编译器产物的死代码。

**教训**：定位到补丁点后，必须往上反汇编 30~60 条指令，确认：

1. 存在一条真实可达的路径能到达该点
2. 到达时用到的寄存器不是刚被 `xor` 清零的
3. 该分支确实影响最终返回值

做不到这三条验证，就不要下"位置正确"的结论。

---

## 九、常用命令

```bash
# 列出所有资格相关函数
python tools/agydis.py --list "ValidateAndOnboard"

# 反汇编整个函数（自动标注 call/jmp 的目标函数名）
python tools/agydis.py --func ValidateAndOnboard

# 找谁调用了它（判断改动影响面）
python tools/agydis.py --callers ValidateAndOnboard

# 搜机器码，看命中在哪些函数里
python tools/agydis.py --search c6400801

# 按 RVA 反汇编一段
python tools/agydis.py 0x229a0d0 0x60
```

`agydis.py` 需要 `pip install capstone`；`fix_agy.py` 不需要任何第三方库。
