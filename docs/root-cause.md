# 根因分析：为什么"账号不合格"是客户端的误判

本文是完整的排查结论与证据链。所有结论都来自**实测**（日志、反汇编、真实令牌调用），
不是推测。如果你要判断某个新出现的故障是否属于同类问题，先读
[log-signatures.md](log-signatures.md)。

---

## 一、结论先行

Antigravity 的登录资格判定有**三层**，历史上先后出过问题：

| 层 | 位置 | 症状 | 是否当前主因 |
|---|---|---|---|
| L1 网络 | Electron 主进程 / agy 不读系统代理 | `connect timeout`、OAuth 无法回调 | 前置条件，需用启动器解决 |
| L2 主进程判定 | `resources/app/out/main.js` 的 `confirmUserForService` | 2.5.x 时代报"不合格" | **已过时**（2.7 已改） |
| L3 侧边栏判定 | `~/.gemini/bin/agy.exe` 的 `ValidateAndOnboardAccount` | 2.7+ 报"不合格" | **当前主因** |

本仓库的核心补丁打在 **L3**。

---

## 二、账号其实是合格的（排除账号因素）

用账号真实的 refresh_token 直接调 `v1internal:loadCodeAssist`，服务端返回：

```json
{
  "currentTier": { "id": "standard-tier", "name": "Antigravity" },
  "paidTier":    { "id": "g1-pro-tier",   "name": "Google AI Pro" },
  "ineligibleTiers": [{
    "reasonCode": "UNSUPPORTED_LOCATION",
    "reasonMessage": "Your current account is not eligible for Antigravity,
                      because it is not currently available in your location.",
    "tierId": "free-tier"
  }]
}
```

读法很关键：

- **`currentTier` = standard-tier**：账号**已经**被授予 Antigravity 正式层
- **`ineligibleTiers` 里只有 `free-tier`**：被地区限制的是**免费层**，不是用户所在的层

也就是说服务端同时表达了"你（付费用户）可以用"和"免费层在你们地区不开放"两件事。
客户端只看了后者。

---

## 三、客户端错在哪

### 3.1 agy 的判定链

OAuth 换码成功后，agy 会：

```
code_exchanged
  → fetchLoadCodeAssistResponse        （10ms 就返回了，说明网络没问题）
  → authclient.ValidateAndOnboardAccount
      ├─ 解析响应，检查 ineligibleTiers
      ├─ 命中 UNSUPPORTED_LOCATION → 构造"不合格"结果
      └─ 丢弃刚拿到的 token（不落盘）
  → 日志写 failure_kind=ineligible
```

证据（`~/.gemini/antigravity/cli.log`）：

```
[auth-login] attempt=3 step=code_exchanged
[auth-login] attempt=3 url=https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist
[auth-login] attempt=3 done ... failure_kind=ineligible outcome=error
```

注意 `loadCodeAssist` 到 `done` 之间只有 **10 毫秒** —— 这不可能是网络超时，
是服务端明确回了"不合格"之后客户端立刻放弃。

### 3.2 函数结构（反汇编结论）

`google3/third_party/jetski/language_server/auth_client/authclient.ValidateAndOnboardAccount`

函数体内有三条出口：

| 出口 | 语义 |
|---|---|
| `fetchLoadCodeAssistResponse` 报错 → `buildErrorResp` → return | 网络/协议错误 |
| `OnboardUser` 报错 → `buildErrorResp` → return | 开通失败 |
| `GetUserStatus` 成功 → 构造 `eligible=true` 对象 → return | **正常放行** |

"合格"标志就是一个布尔字段：

```asm
lea  rax, [type_of_result]      ; 48 8D 05 <disp32>
call runtime.newobject          ; E8 <rel32>
mov  byte ptr [rax+8], 1        ; C6 40 08 01   <- eligible = true
```

当服务端响应里带 `ineligibleTiers` 时，执行流会走到"构造不合格对象"，
上层再用 `cmp byte[rcx+8], 0` 判断，读到 0 就发布不合格状态。

### 3.3 为什么"只把失败分支改成成功"不够

曾尝试过一条更"温和"的补丁：只把两个 `jcc` 改成 `jmp`
（`OnboardUser` 失败 → 走成功块，`GetUserStatus` 失败 → 走成功块），
保留真实流程。结果：

- ✅ 模型目录完整（33 个，含 hydrogen）
- ❌ `GetAuthStatus` 仍返回 `ineligible` → 面板依然拦住用户

原因是"是否合格"这个状态**不是**由那两个分支的返回值决定的，而是由更上游的
`ineligibleTiers` 解析结果决定的。改分支不改变结论。

所以最终选择**入口接管**：函数一进来就跳到"构造合格对象"的位置，跳过整个判定。

---

## 四、入口接管补丁

### 4.1 补丁内容

```
函数入口 file 0x2299280（1.5.10）:
  原始: 4C 8D 64 24 A8 ...            (lea r12,[rsp-0x58] ...)
  补丁: 55 48 89 E5 E9 51 04 00 00    (push rbp; mov rbp,rsp; jmp +0x451)
```

- `push rbp; mov rbp,rsp` 造一个合法栈帧
- 目标块末尾是 `leave; ret`，`leave` 会从 `rbp` 恢复 `rsp` —— 所以栈是平衡的
- `jmp rel32` 不破坏 16 字节对齐（不像 `call` 会压栈）
- 目标块只用 `rax` 做返回，不依赖被跳过的 prologue 里的 `r12`

实测：agy 重启后 `initialized server successfully`，`crashes/` 无新 panic，
`hasValidAuth: true`，模型 36 个。

### 4.2 为什么必须打在入口

用符号表扫过调用点，`ValidateAndOnboardAccount` 有 **4 个真实调用者**：

```
authclient.(*PersonalAuthValidator).Validate
backend.(*ServerBackend).ValidateAndOnboardAccount
backend.(*ServerBackend).ValidateAndOnboardAccount-fm
acp/server.serverTurns.ValidateAndOnboardAccount
```

只改其中一条分支，其它路径仍会得出"不合格"。入口接管一次覆盖全部。

### 4.3 版本自适应

补丁点**不写死偏移**。`fix_agy.py` 每次运行都：

1. 解析 PE 里的 Go `pclntab`（magic `F1 FF FF FF`）
2. 按名字找 `...ValidateAndOnboardAccount`
3. 在函数体内搜 `C6 40 08 01`，并要求前 12 字节是
   `lea rax,[rip+x]`(7) + `call`(5)
4. 用 `RVA = entryoff + 0x1000`、`file = RVA - 0xA00` 换算

所以 agy 升级后通常**直接可用**，无需重新逆向。

---

## 五、反复"修好又坏"的真正原因

不是补丁失效，是**文件被换掉了**。

agy 会静默自动升级，1.5.9 → 1.5.10 时：

```
agy.version.json   1.5.9 → 1.5.10
文件大小           231,726,744 → 233,313,944
入口字节           554889e5… → 4c8d6424a8…（恢复原厂）
```

整段代码还整体下移了 `0x8FE60`，旧偏移全部失效。

对策是三道防线（见 [AGENT.md 第 4 节](../AGENT.md#4-防复发打完补丁必做)）：

1. `AGY_CLI_DISABLE_AUTO_UPDATE=1`（用户级环境变量 + 启动器双重注入）
2. `agy.exe` 设只读（更新器无法替换）
3. `fix_agy.py` 版本自适应（万一还是被换，一条命令重打）

---

## 六、模型列表与登录状态的关系

排查过程中出现过"登录成功但没有 Gemini 4 Hydrogen"的现象，原因有两个，都**不是账号问题**：

1. **刚登录时目录是残缺的**：拿到的模型列表只有 26 个、无 hydrogen。
   令牌落盘 + agy 重启 + 面板 Reload Window 后补全到 33/36 个。
2. **入口接管的副作用**：接管后 `SetProjectID("")`、`SetUserTier("")` 为空，
   首次拉取会得到残缺目录。

判断标准：**看有没有 `gemini-4-hydrogen-*`，不要看条数**。
`agy models` 走 CLI 链路，与面板链路是两套目录，可以互相印证：

```bash
agy models                                      # CLI 视角
python tools/verify_hub.py                      # 面板视角
agy -p "reply OK" --model gemini-4-hydrogen-medium   # 实际可用性
```

---

## 七、已排除的方向（别再重复）

| 猜想 | 反证 |
|---|---|
| 账号没有 Antigravity 资格 | 服务端返回 `currentTier=standard-tier` |
| 代理节点在受限地区 | 出口 IP 美国；`loadCodeAssist` 10ms 返回 |
| 需要清缓存 / 重装 IDE | 判定发生在服务端响应之后的客户端逻辑里 |
| 需要删除 Gemini Code Assist 连接 | 无关，判定不查这个 |
| 换 `jetski.cloudCodeUrl` 端点 | 只对主进程生效，管不到 agy |
| IDE 版本太旧 | 官方稳定通道返回的 commit 与本机一致 |
| 是 Google 服务端 503 | 503 是另一类故障，日志会明确写 `UNAVAILABLE` |

---

## 八、证据文件位置

| 内容 | 路径 |
|---|---|
| 登录链路日志 | `%USERPROFILE%\.gemini\antigravity\cli.log` |
| IDE 主进程 auth 日志 | `%APPDATA%\Antigravity IDE\logs\<会话>\auth.log` |
| agy 版本与校验信息 | `%USERPROFILE%\.gemini\bin\agy.version.json` |
| hub RPC 端口与 CSRF | `%USERPROFILE%\.gemini\antigravity\daemon\hub_<port>.json` |
| 崩溃记录 | `%USERPROFILE%\.gemini\antigravity\crashes\`（空文件 = 无 panic） |

原始逐日排查记录见 [progress-archive.md](progress-archive.md)。
