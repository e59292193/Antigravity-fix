# AGENT.md — 自动化修复执行手册

> 本文件供 **AI Agent** 直接执行。人类用户请看 [README.md](README.md)。
>
> 目标环境：Windows x64。所有命令在 Git Bash 或 PowerShell 下均可运行，
> 路径用 `%USERPROFILE%` / `$env:USERPROFILE` 表达，不依赖具体用户名。

---

## 0. 任务定义

用户报告：Antigravity IDE 登录失败，提示 `this account is ineligible to use Antigravity`，
或侧边栏长期处于未登录/不合格状态。

你要做的是：**判定失败属于哪一类，并按对应分支修复，最后用实测数据证明登录链路已通。**

**不要**在没有诊断的情况下直接执行修复脚本。
**不要**建议用户重装 IDE 或换账号 —— 历史排查已证明这两条都无效。

---

## 1. 前置检查（必须全部通过再往下走）

```bash
python -c "import sys;print(sys.version)"          # 需要 Python 3.8+
ls -l "$USERPROFILE/.gemini/bin/agy.exe"           # 必须存在
```

若 `agy.exe` 不存在：说明这台机器还没跑过侧边栏后端。
→ 让用户启动一次 IDE，点一次 `Sign in with Google`，等 30 秒（agy 会自动下载），
然后重新执行第 2 步。**不要**尝试手动下载 agy。

确认 IDE 安装位置（用于第 6 步重启）：

```bash
ls "$LOCALAPPDATA/Programs/Antigravity IDE/Antigravity IDE.exe" 2>/dev/null
ls "/d/APP/Antigravity IDE新版/Antigravity IDE/Antigravity IDE.exe" 2>/dev/null
```

---

## 2. 诊断

```bash
python tools/diagnose.py
```

它会输出一段报告，最后一行形如 `VERDICT: <结论码>`。按结论码分诊：

| 结论码 | 含义 | 跳到 |
|---|---|---|
| `OK` | 补丁在位、登录态有效 | 第 7 步验证 |
| `NO_AGY` | `agy.exe` 不存在 | 回第 1 步 |
| `PATCH_MISSING` | agy 是原厂状态，补丁未打 | 第 3 步 |
| `PATCH_STALE` | 补丁在，但 `version.json` 与实际文件不一致 | 第 3 步（脚本会自动同步） |
| `AUTOUPDATE_ON` | 补丁在，但自动更新没关，随时会被覆盖 | 第 4 步 |
| `LOG_INELIGIBLE` | 日志里最近一次登录仍报 `failure_kind=ineligible` | 第 3 步；若补丁已在位则跳第 8 步 |
| `PROXY_DOWN` | 本机代理端口未监听 | 第 5 步 |
| `LOG_TIMEOUT` | 日志出现 `connect timeout` / `UND_ERR_CONNECT_TIMEOUT` | 第 5 步 |

`diagnose.py` 只读，不修改任何文件，可以放心反复运行。

---

## 3. 打补丁（核心步骤）

```bash
python tools/fix_agy.py            # 打补丁；已打过则自动跳过
python tools/fix_agy.py --check    # 只看状态，不写
python tools/fix_agy.py --dry-run  # 打印将要改的字节，不落盘
```

正常输出示例：

```
函数入口 RVA 0x2299c80 (file 0x2299280) size 0x600
合格块 RVA 0x229a0da
入口当前字节: 4c8d6424a8...
写入补丁: 554889e5e9...  (rel=0x451)
已备份 -> agy.exe.<sha8>bak
完成：已打补丁并设为只读
```

**它会做什么**：

1. 解析 PE 的 Go `pclntab`，按函数名找到 `authclient.ValidateAndOnboardAccount`
2. 在函数体内定位 `mov byte ptr [rax+8], 1`（合格对象构造标志）
3. 把函数入口 9 字节改写为 `push rbp; mov rbp,rsp; jmp <合格块>`
4. 备份原厂文件、同步 `agy.version.json` 的 size/sha512、设为只读
5. 若文件被运行中的进程锁住，自动降级为"改名换文件"（不需要关 IDE）

**若报"目标函数未找到"**：说明 agy 版本结构变化较大。
→ 跳第 8 步手动重新定位，**不要**盲目扫字节乱改。

打完补丁后必须让 agy 重新加载：

```bash
python tools/fix_agy.py --restart
```

`--restart` 会结束 agy 进程，IDE 会在几秒内自动拉起新实例。
若 10 秒后 agy 没有重新出现，说明 IDE 未运行 —— 让用户启动 IDE。

---

## 4. 防复发（打完补丁必做）

agy 会**静默自动升级**，升级会把补丁整个覆盖掉（这是历史上"修好了又坏"的唯一原因）。
四道防线，全部要有：

```bash
# 防线 1：用户级环境变量（对所有后续进程生效）
setx AGY_CLI_DISABLE_AUTO_UPDATE 1

# 防线 2：只读属性（fix_agy.py 已自动设置，用这条确认）
attrib +R "%USERPROFILE%\.gemini\bin\agy.exe"
attrib "%USERPROFILE%\.gemini\bin\agy.exe"     # 输出应带 R

# 防线 3：从启动器启动 IDE（启动器里也注入了该变量）

# 防线 4：自愈守护（最可靠的一道，必须装）
python tools/heal_agy.py            # 立即检查，缺补丁就重打
pythonw tools/heal_watch.py --rounds 12 --interval 30   # 后台盯 6 分钟
```

防线 4 的落地方式（三选一，推荐全上）：

- 启动器里 `start "" pythonw tools\heal_watch.py`（IDE 起完再盯梢）
- 登录自启：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
- 能建计划任务的环境：登录触发 + 每 10 分钟重复

验证防线 1 已落地：

```bash
reg query "HKCU\Environment" /v AGY_CLI_DISABLE_AUTO_UPDATE
```

> **已知失效案例**：2026-11-19 开机后 agy 从 1.5.10 自动升到 1.5.12，防线 1 没能拦住
> （hub 启动路径未走该分支），只读属性也被"改名+换文件"绕过。
> 所以**不要只依赖防线 1/2**，防线 4 才是兜底。

> **禁止用 ACL（`icacls /deny`）锁 `agy.exe`**：icacls 生成的拒绝 ACE 会连带
> `SYNCHRONIZE`，文件连读都会被拒；且拒绝项优先级高于"所有者隐含权限"，
> 事后 `Set-Acl` 都改不回来，只能整目录改名绕开，会留下删不掉的 200MB+ 垃圾。

---

## 5. 网络层（仅当结论码是 PROXY_DOWN / LOG_TIMEOUT）

agy 与 Electron 主进程**都不读 Windows 系统代理**，只认环境变量，且 Electron 主进程
还需要 `NODE_USE_ENV_PROXY=1` 才会让 `fetch` 走代理。

用仓库自带启动器代替双击 exe：

```bash
python tools/launch_ide.py --proxy http://127.0.0.1:7892
```

`launch_ide.py` 会自动发现 IDE 安装路径与可用代理端口，注入：

```
HTTP_PROXY / HTTPS_PROXY / ALL_PROXY / NO_PROXY
NODE_USE_ENV_PROXY=1
AGY_CLI_DISABLE_AUTO_UPDATE=1
```

并以 `--proxy-server=http://127.0.0.1:<port>` 启动。

> **踩坑**：只设环境变量不加 `--proxy-server` 时，Electron 会把
> `https://127.0.0.1:<语言服务器端口>/` 的本地 UI 请求也送进代理，导致白屏
> `ERR_TIMED_OUT`。`--proxy-server` 让 Chromium 隐式绕过 loopback，是必需的。

---

## 6. 重启与刷新

1. 结束 IDE：`taskkill /F /IM "Antigravity IDE.exe"`
2. 用启动器启动：`python tools/launch_ide.py`
3. 用户点 `Sign in with Google` 完成浏览器授权
4. IDE 内 `Ctrl+Shift+P` → `Reload Window`（让面板重新拉取模型目录）

---

## 7. 验收标准（必须全部满足）

```bash
python tools/verify_hub.py
```

期望输出：

```
hasValidAuth: true
model_count: 36
hydrogen: gemini-4-hydrogen-low, -medium, -high, -xhigh, -sonar, -tiered-high, -tiered-xhigh
```

同时交叉核对日志：

```bash
tail -40 "$USERPROFILE/.gemini/antigravity/cli.log" | grep -E "has_auth_token|failure_kind|get_auth_status"
```

必须满足：

- [ ] `get_auth_status valid=true` 或 `hasValidAuth: true`
- [ ] 最近一次登录**没有** `failure_kind=ineligible`
- [ ] `has_auth_token has_token=true`（token 已落盘）
- [ ] 模型列表包含 `gemini-4-hydrogen-*`

> 模型条数会随版本变化（33 / 36 都见过）。**判断标准是"有没有 hydrogen"，
> 不是具体条数。** 刚登录后的第一次查询可能是残缺目录，重载窗口再查即可。

---

## 8. 换版本后重新定位（补丁点失效时的唯一正确做法）

agy 是 Go 程序且已 strip，`go tool nm` 不可用。用 `pclntab` 自解析：

```bash
python tools/agydis.py 0x2299c80 0x600        # 反汇编指定 RVA 范围，自动标注 call/jmp 目标函数名
```

关键常量与换算（在 `fix_agy.py` 里已实现，这里是原理）：

| 项 | 值 |
|---|---|
| PE 节映射 | `.text` VA=`0x1000`，raw=`0x600` ⇒ **file = RVA − 0xA00** |
| pclntab magic | `F1 FF FF FF`（后随 `00 00` 与 minLC） |
| 指针宽度 | `pclntab[7] == 8` |
| 头部字段 | `nfunc@pclntab+8`；`funcnameOffset` 与 `pclnOffset` 位置随 Go 版本变化（见下） |
| 布局 A（1.5.9） | funcname@+24, pcln@+56 |
| 布局 B（1.5.10） | funcname@+32, pcln@+64 |
| entryoff → RVA | `RVA = entryoff + 0x1000` |
| functab | `base = pclntab + pclnOffset`，每项 8 字节：`<uint32 entryoff, uint32 funcoff>` |

**布局判别技巧**：两种布局都试一遍，用"第一个函数名能否解析成含 `.` 或 `/` 的合法字符串"
来判定哪个对。`fix_agy.py` 的 `layout()` 就是这么做的。

**找合格块的技巧**：不要写死偏移，搜 `mov byte ptr [rax+8], 1` 的机器码
`C6 40 08 01`，并要求其前 12 字节是 `48 8D 05 <disp32>`（`lea rax,[type]`，7 字节）
紧跟 `E8 <rel32>`（`call runtime.newobject`，5 字节）。这 12 字节是"分配对象并置合格位"的固定形态。

细节与踩坑见 [docs/reverse-engineering.md](docs/reverse-engineering.md)。

---

## 9. 回滚

```bash
python tools/fix_agy.py --restore        # 自动挑选最新备份还原
```

或手动：

```bash
attrib -R "%USERPROFILE%\.gemini\bin\agy.exe"
copy /Y "%USERPROFILE%\.gemini\bin\agy.exe.*bak" "%USERPROFILE%\.gemini\bin\agy.exe"
```

回滚后登录会重新失败，但**不会损坏账号或数据**。回滚只影响本地一个 9 字节补丁。

---

## 10. 禁止事项

- ❌ 不要把打补丁后的 `agy.exe` 提交到仓库（233MB，且含 Google 二进制）
- ❌ 不要在 `agy.exe` 未下载时伪造文件
- ❌ 不要改 `version.json` 的 `version` 字段（只同步 size/sha512/mtime）
- ❌ 不要对其它 exe 套用同样的字节模式（`C6 40 08 01` 在别的 Go 程序里含义不同）
- ❌ 不要把 `NODE_USE_ENV_PROXY=1` 当成万能解 —— 它只解决主进程 fetch 走代理，
  不影响 agy 的资格判定

---

## 11. 快速命令卡

```bash
python tools/diagnose.py                    # 诊断
python tools/fix_agy.py                     # 打补丁
python tools/fix_agy.py --restart           # 让 agy 重载
python tools/fix_agy.py --check             # 查状态
python tools/fix_agy.py --restore           # 回滚
setx AGY_CLI_DISABLE_AUTO_UPDATE 1          # 关自动更新
python tools/launch_ide.py                  # 带代理启动 IDE
python tools/verify_hub.py                  # 验收
```

---

## 附：已知副作用与限制

**副作用（可接受，且会自恢复）**

1. 入口接管跳过整个 `ValidateAndOnboardAccount`，因此 `SetProjectID("")`、
   `SetUserTier("")` 为空 —— 日志里能看到这两个空值，属预期。
2. 刚登录完第一次拉取的模型目录可能残缺（26 个、无 hydrogen）。
   令牌落盘 + agy 重启 + `Reload Window` 后补全到 33/36 个。
3. `version.json` 的 `sha512` 会是补丁后文件的哈希，与官方发布值不同。
   这会让版本校验认为"文件与官方不一致"，但 agy 不会因此拒绝启动。

**限制**

1. 仅 Windows x64（依赖 PE 节映射 `RVA − 0xA00`）。
2. 只绕过客户端误判，不改变服务端授权；账号没有资格时依然用不了。
3. agy 大版本重构（函数改名/合格块形态变化）时需按第 8 步重新定位。
4. 只读属性可被 `attrib -R` 或管理员权限解除；不是安全边界，只是防误覆盖。
