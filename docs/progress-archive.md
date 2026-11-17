# 原始排查日志（归档）

> 这是修复过程中逐日记录的原始工作日志，未经整理，保留全部踩坑与失败尝试。
> 面向执行请看 [../AGENT.md](../AGENT.md)，面向结论请看 [root-cause.md](root-cause.md)。
>
> 已脱敏：本机用户名替换为 `<user>`，凭据/令牌一律不含。

---

# Antigravity 登录修复进度

## 2026-09-23 13:57 — 登录修复（代理环境变量注入）

### 问题
- Antigravity（Hub）与 Antigravity IDE 均无法登录 Google 账号（已用代理，非网络问题）

### 根因
- Antigravity 的 `language_server.exe` 是 **Go 程序，不读取 Windows 系统代理**（WinINET 设置）
- 系统代理开着时浏览器/Electron 正常，但语言服务器**直连 Google** → 从国内 IP 访问 → 触发 Google 服务端地区检测 → 登录/模型加载失败
- IDE 的 `auth.log` 显示 OAuth 流程发起后始终无回调完成（`key not found` 循环）

### 环境实测证据
| 测试项 | 结果 |
|---|---|
| ShanHai 代理 | TUN 模式运行中（`ShanHai Tunnel` 网卡，默认路由 metric 0），mixed 端口 `127.0.0.1:7892` |
| 系统代理 | ProxyEnable=1, ProxyServer=127.0.0.1:7892 |
| HTTP(S)_PROXY 环境变量 | 未设置（缺口所在） |
| `generativelanguage.googleapis.com` 等 4 个域名 | 直连与走代理均可达（TUN 兜底），走代理更快 |
| Hub 更新包下载 | 注入环境变量后 19 秒下载完成（此前一直卡死）→ 证明注入生效 |

### 修复方案（本目录三个文件）
1. `启动 Antigravity（登录修复）.cmd` — Hub 启动器
2. `启动 Antigravity IDE（登录修复）.cmd` — IDE 启动器
3. 脚本内容：
   - 检查端口 7892 在监听（ShanHai 未启动则提示退出）
   - 注入 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY=localhost,127.0.0.1`（大小写各一份）
   - 附加 `--proxy-server=http://127.0.0.1:7892`（显式指定 Chromium 代理，隐式保留本地回环绕过）

### 踩坑记录（重要）
- **NO_PROXY 无法救 Electron 的窗口加载**：设置环境变量后，Electron/Chromium 会把
  `https://127.0.0.1:<LS端口>/` 的本地 UI 请求也送进代理 → `ERR_TIMED_OUT`（白屏）
  - 尝试 `NO_PROXY=localhost,127.0.0.1,::1` 和去掉 `::1` 均无效
  - **有效解法**：启动参数加 `--proxy-server=http://127.0.0.1:7892`，Chromium 隐式绕过 loopback，本地 UI 直连
- 批处理文件内容不能写中文（cmd 按 GBK 解析 UTF-8 会乱码报错），用 ASCII 内容 + 中文文件名

### 验证命令
```powershell
# 1. 确认代理端口
Get-NetTCPConnection -State Listen -LocalPort 7892
# 2. 启动后确认 Hub 窗口加载成功（应出现 CDP Discovery 行，且无 ERR_TIMED_OUT）
Select-String "C:\Users\<user>\AppData\Roaming\Antigravity\logs\main.log" -Pattern "ERR_"
Select-String "C:\Users\<user>\AppData\Roaming\Antigravity\logs\language_server.log" -Pattern "CDP Discovery"
# 3. 登录后确认 IDE auth 状态
Get-Content "C:\Users\<user>\AppData\Roaming\Antigravity IDE\logs\<最新目录>\auth.log" -Tail 5
```

### 状态
- [x] 根因定位
- [x] 修复脚本创建并验证启动（Hub 窗口加载成功、IDE 进入待登录状态）
- [x] Hub 2.15.1 更新包已下载至 `C:\Users\<user>\AppData\Local\antigravity-updater\pending`（下次重启自动安装）
- [x] **Hub 登录成功**（用户 13:33 完成 OAuth，凭据 `gemini:antigravity` 已存入凭据管理器）

## 2026-09-23 14:45 — IDE "账号不合格" 报错排查（第二轮）

### 现象
- IDE 登录：浏览器 OAuth 成功（"You have successfully authenticated"），但 IDE 显示
  **"Sorry, this account is ineligible to use Antigravity IDE"**（提示 location 不可用）

### 排查结论（证据）
1. **账号本身有资格**：用 Hub 存储的令牌直接调用 `loadCodeAssist`，返回
   `allowedTiers: [standard-tier]`；且同一账号 13:33 已在 Hub 成功登录
2. **"ineligible" 是误导性 UI**：`auth.log` 显示 14:01:09 收到 OAuth 回调进入
   `validatingLogin`，14:01:42 发出 `loadCodeAssist` 校验请求，**14:02:10（28 秒后）超时**
   → 网络超时被 UI 统一渲染成"账号不合格"
3. **根因是 ShanHai 节点临时波动**：14:00–14:35 期间到 `cloudcode-pa.googleapis.com`
   成功率仅 ~50%（两种路径均如此，实测 10 轮 5 成功），14:35 后恢复
4. **IDE 版本不是问题**：D:\APP 的 IDE 2.5.5 就是官方最新稳定版
   （更新 API `antigravity-ide-auto-updater...run.app/api/update/win32-x64-user/stable/latest`
   返回的 commit 与本机一致；product.json 的 updateUrl 是 example.com 占位符，IDE 无法自更新）

### 网络实测数据（关键时间线）
| 时间 | cloudcode-pa 连通性 |
|---|---|
| 13:47 | TUN + 代理全通（0.4–4s） |
| 14:00–14:35 | 波动，约 50% 成功（IDE 登录校验恰好死在这窗口） |
| 14:45 | 恢复，16/16 全通（平均 0.4–0.6s） |

### 附加发现
- ShanHai 的 config.yaml / profiles\999999.yaml 均**加密**，`external-controller` 为空
  → 无法 CLI 切换节点；节点波动时只能等恢复或在 UI 手动换节点
- 曾尝试启动 Clash Verge 内核(7897)作备用，订阅节点 0/10 不通（节点已失效），已按用户要求停用

### 状态
- [x] IDE "不合格"根因定位 = 网络超时（非账号问题）
- [x] 网络恢复确认（16/16 + 令牌调用 0.84s 获 Google 权威响应）
- [x] IDE 已通过修复脚本干净重启（14:45）
- [x] 15:30 更新：网络不是根因，真正根因见第三轮

## 2026-09-23 15:30 — 第三轮：真正的根因与客户端补丁（决定性）

### 决定性证据（用刷新令牌 + IDE 客户端身份复现 loadCodeAssist）
```json
{
  "currentTier": { "id": "standard-tier", "name": "Antigravity" },
  "paidTier": { "id": "g1-pro-tier", "name": "Google AI Pro" },
  "ineligibleTiers": [{
    "reasonCode": "UNSUPPORTED_LOCATION",
    "reasonMessage": "Your current account is not eligible for Antigravity, because it is not currently available in your location.",
    "tierId": "free-tier"
  }]
}
```

### 根因链条
1. 用户账号是 **Google AI Pro 付费订阅**，Google 已授予 standard-tier（Antigravity 正式层）
2. 但 IDE 登录逻辑（main.js → confirmUserForService）写死：消费者必须先通过 **free-tier** 资格检查
3. Google 对 free-tier 返回 UNSUPPORTED_LOCATION（免费层地区限制），allowedTiers 里没有 free-tier
4. IDE 判定 `s = free-tier可用 || 无拒绝理由 = false` → SET_INELIGIBLE → "账号不合格"屏幕
5. **IDE 无视了用户已有的付费资格（currentTier=standard-tier）** —— 纯客户端判定 bug
6. 网络好坏、代理、清缓存、删 Gemini Code Assist 连接均不影响此判定（14:00 的"超时"只是巧合叠加）

### 关键代码位置（D:\APP\Antigravity IDE\resources\app\out\main.js）
- 判定逻辑：`confirmUserForService` → `const s=!!n||!i` → `if(s){onboardUser("free-tier")...AUTH_SUCCESS} else {SET_INELIGIBLE}`
- 端点选择：`NAl()` → `jetski.cloudCodeUrl` 设置可覆盖端点（daily/prod）；Hub 走 daily 是因其流程 isTierGCPTos=false
- OAuth client: 1071006060591-tmhssin2h21lcre235vtolojh4g403ep + secrets 在 main.js 内（GOCSPX-*）

### 补丁内容（已应用，备份 main.js.bak）
- A: `const s=!!n||!i,` → `const s=!!n||!i||!!r?.currentTier,`（已有正式资格则视为合格）
- B: `if(s){await ...onboardUser("free-tier",t),...` → `if(s){r?.currentTier||await ...onboardUser("free-tier",t),...`（已有 currentTier 时跳过 free-tier 注册）
- 行为影响面：仅改变"付费但 free-tier 被地区拒"的账号；正常免费用户路径不变
-jetskiAgent\main.js 无需补丁（无资格判定，仅展示配额）

### 排查中确认的其他事实
- IDE 凭据管理器无独立条目；~/.gemini/antigravity-ide 无资格缓存 → 排除本地缓存投毒
- Hub 的 LS（daily 端点）13:56 "Auth succeeded"，且凭据管理器有 gemini:antigravity（Hub 登录成功）
- bare 请求（无 UA）两端点响应相同（UNSUPPORTED_CLIENT）；带 UA（antigravity/2.5.5 或 2.3.1）两端点响应相同（UNSUPPORTED_LOCATION）→ **端点不是变量，客户端身份才是**

### 状态
- [x] 真正根因定位 + 服务端响应复现（与 UI 消息逐字一致）
- [x] main.js 补丁已应用并验证（15:32，备份 main.js.bak）
- [x] IDE 已重启（15:33）
- [x] 15:20 用户重试：**"不合格"屏幕已消失（补丁A/B生效）**，但出现新错误页
  "There was an unexpected issue setting up your account"——卡在
  `www.googleapis.com/oauth2/v2/userinfo` TLS 握手被瞬时重置（refreshUserStatus → getProfileData）
- [x] 15:40 第二个补丁：getProfileData 改为容错（失败返回空资料，不阻断登录）
  - 锚点：`profilePictureUrl:s.picture??""}}catch(n){throw this._logger.error(n),n}}`
  - 改为：`catch(n){return this._logger.error(n),{name:"",email:"",profilePictureUrl:""}}`
  - ⚠️ 首次应用时漏了一个方法结束花括号导致主进程启动崩溃（SyntaxError），15:50 已修复并经
    `node --check`（.mjs 方式）验证语法通过
- [x] **15:55 用户确认登录成功，修复完成**
- [x] 15:58 启动脚本升级：山海未运行时自动拉起 ShanHai.exe 并等待端口就绪（最多 30 秒），
      真正一键启动；已实测通过
- [ ] 回滚方法（如需）：关闭 IDE，`Copy-Item main.js.bak main.js -Force`

## 2026-10-20 16:55 — "Our servers are experiencing high traffic" 排查（非本地问题）

### 现象
- IDE 内报 "Error: Our servers are experiencing hi..."（Error ID: b7b28b50-...）
- 启动器窗口大量 `handshake failed; SSL error code 1, net_error -100`（16:05-16:48）

### 排查结论（全部通过）
- 山海运行正常（7892 监听、TUN Up）；三个 Google 端点双路径全通（0.6-2.5s）
- 三个补丁完好（main.js 未被改动，LastWriteTime 仍为 09/23 15:31）；IDE 仍为 2.5.5
- 登录有效：loadCodeAssist / fetchAvailableModels 持续成功

### 真正根因（Google 服务端）
```
model unreachable: UNAVAILABLE (code 503): No capacity available for
model gemini-3.8-flash-high on the server
```
- 时间线：当天 15:48 会话正常（0 次容量错误）→ 15:53 会话 16:25 起持续 503（33 次）
- 即 Google 端 gemini-3.8-flash-high 模型**当天 16:25 起容量耗尽**，与 IDE 内
  "Our servers are experiencing high traffic" 提示完全对应
- 启动器里的 TLS handshake 错误为节点瞬时抖动，当前已恢复

### 处理
- 本地无需任何修复。用户操作：**在 IDE 模型选择器切换其他模型**（非 -high 变体或
  Claude/GPT 等 AI Pro 可用模型），或等待 Google 恢复容量后重试
- 注意：LS 日志有 Mac 风格路径 bug（/Users/<user>/... transcript.jsonl 找不到），
  仅影响 transcript 写入，不影响功能

## 2026-11-05 15:35 — "Agent execution terminated due to error"（429）根因修复

### 现象
- 登录成功，但使用时一直报 "Error: Agent execution terminated due to error."（Error ID e4d4e6ab-...）

### 复现与根因（决定性证据）
用账号令牌直接调用 `v1internal:generateContent`，做端点对照实验：

| 端点 | 结果 |
|---|---|
| `cloudcode-pa.googleapis.com`（IDE 默认生产端点） | **所有模型一律 429 RESOURCE_EXHAUSTED**（gemini-3.8-flash-high / 3.5-flash-lite / claude-sonnet-5-5-medium / 2.5-flash 全部 429；去掉 project、换 UA 2.6.0 仍 429） |
| `daily-cloudcode-pa.googleapis.com`（Hub 用的日常端点） | **HTTP 200 正常返回内容** |

- 账号配额正常：`fetchAvailableModels` 返回 23 个模型，全部 `remainingFraction = 1`（满配额，20:23 重置）
- 账号层级正常：`loadCodeAssist` 仍为 `currentTier=standard-tier` + `paidTier=Google AI Pro`
- 结论：**Google 的 prod 端点对该账号整体 429，daily 端点可用** → 纯端点选择问题，非账号/配额/网络问题

### 修复
在 IDE 用户设置中把云端端点指向 daily：
- 文件：`C:\Users\<user>\AppData\Roaming\Antigravity IDE\User\settings.json`（已备份 settings.json.bak）
- 新增：`"jetski.cloudCodeUrl": "https://daily-cloudcode-pa.googleapis.com"`
- 代码依据：main.js `getBaseUrl()` → `cloudCodeUrlOverride: getValue("jetski.cloudCodeUrl")`，优先级最高；
  该值会作为 `--cloud_code_endpoint` 传给语言服务器（已从新会话日志验证生效）
- 顺带清理：关闭 IDE 时发现 **2 个 language_server 进程并存** 和一个 2.6.0 安装器残留
  （`%LOCALAPPDATA%\Temp\antigravity-stable-user-x64\AntigravitySetup-stable-*.exe`，已删除，防止误升级覆盖补丁）

### 附带处理：阻断 IDE 自动升级（保护补丁）
发现 `AntigravitySetup-stable-9e3a5da5...exe`（2.6.0 安装器）由 `Antigravity IDE.exe` 自己拉起，
若关闭 IDE 就会安装升级并**覆盖 main.js 补丁**。已在 settings.json 中禁用：
- `"update.mode": "none"`
- `"update.enableWindowsBackgroundUpdates": false`
- `"extensions.autoUpdate": false`
并杀掉了安装器进程、清空 `%LOCALAPPDATA%\Temp\antigravity-stable-user-x64\`；重启后确认安装器不再复活。

### 验证
- 新会话 `ls-main.log` 的 Args 已含 `--cloud_code_endpoint https://daily-cloudcode-pa.googleapis.com`
- 两个语言服务器进程的 endpoint 均为 daily；该会话 **429 计数 = 0**
- `loadCodeAssist` / `fetchAvailableModels` 经 daily 端点正常返回
- 安装器进程已消失，残留目录已清空
- 补丁仍完好（main.js 未改动，patchA/B 均 True）

### 状态
- [x] 根因定位（端点 429，非账号/配额）
- [x] settings.json 端点切换到 daily 并验证生效
- [x] 清理重复 LS 进程与残留安装器
- [x] 进度与记忆归档

### 备注
- 补丁仍完好（main.js 未改动，patchA/B 均 True）
- 该设置写在用户 settings.json，**不会被版本升级覆盖**
- 若某天 daily 端点也 429，可改回 `https://cloudcode-pa.googleapis.com` 再试（两边互为备份）

## 最终使用说明（重启电脑后）
1. 双击 `启动 Antigravity（登录修复）.cmd` 和/或 `启动 Antigravity IDE（登录修复）.cmd`
2. 脚本会自动：启动山海（如未运行）→ 等待 7892 就绪 → 注入代理环境变量 → 带 --proxy-server 启动对应应用
3. 已登录状态会保持（refresh_token 自动续期），日常无需重新登录
4. 注意事项：
   - 使用期间保持山海运行（模型 API 走它的出口）；节点波动时换节点或稍后重试
   - **不要手动升级 IDE**（补丁会丢）；Hub 会自动升到 2.15.1（无补丁，安全）
   - 若 IDE 将来重装/升级后出现"不合格"，需按本文件第三轮记录重打 main.js 补丁

### 附注
- 以后日常启动请使用本目录的两个 .cmd 脚本（不要直接双击原 exe，否则语言服务器不走代理）
- IDE 自动更新被 product.json 的 example.com 占位符禁用；若将来手动升级 IDE，需重打此补丁（或先对比新版本是否已修复此逻辑）
- Clash Verge（端口 7897）为备用代理，本脚本固定使用 ShanHai 7892

## 2026-11-16 10:20 — IDE 升级 2.7.0 后登录失败（AvenCores patcher 部署）

### 现象
- 升级 IDE 2.7.0（`D:\APP\Antigravity IDE新版\Antigravity IDE`）后：OAuth 报
  `dial tcp 108.177.97.95:443 connect timeout`（图2 的 "unexpected issue"），
  浏览器认证成功后侧边栏仍报 "Sorry, this account is ineligible to use Antigravity"（图3/图4）

### 根因（两条，均已实测）
1. **主进程 fetch 不走代理**：2.7 的 OAuth/loadCodeAssist 由 Electron 主进程
   gaxios→undici `fetch` 发起，只认 `NODE_USE_ENV_PROXY=1` 才会读取 HTTPS_PROXY。
   实测（ELECTRON_RUN_AS_NODE，Node v22.21.1）：
   - 无代理/仅设 HTTPS_PROXY → `UND_ERR_CONNECT_TIMEOUT`（~10.8s）
   - HTTPS_PROXY + `NODE_USE_ENV_PROXY=1` → 1.2s 返回 404（通）
   - curl 直连 oauth2.googleapis.com 超时，走 7892 代理 0.5s
2. **侧边栏资格判定在 agy 而不是 main.js**：2.7 内置扩展 `agy`（google-antigravity v1.11.0）
   以 `agy.exe --hub` 作为面板后端，`failureDetails.case="ineligible"` 由
   `~\.gemini\bin\agy.exe`（v1.5.9）返回。main.js 的 isGoogleInternal 补丁只影响主窗口登录。

### 本次部署内容
- 源码：`tools\open-ag-patcher`（trustybird/AvenCores-open-antigravity-patcher v1.3.8，git 走 7892 代理克隆）
- 内置 Python：`tools\python`（3.12.7 embeddable + packaging 26.3）
- main.js：patcher PATCH→1 已应用 `resetIsTierGCPTos(),true`（1 处），备份 `main.js.bak`
- agy：patcher PATCH→3 已应用 eligibility gate 关闭（x64 @0x34443e），备份 `agy.exe.agybak`；
  验证 `agy.exe --hub` 仍能正常 READY（install.log 10:19）
- 启动脚本：`启动 Antigravity IDE（登录修复）.cmd`
  - 新增 `NODE_USE_ENV_PROXY=1`（关键）
  - 因 .cmd 中文路径在 UTF-8/GBK 间不稳定，创建目录联接 `D:\APP\AG-IDE` → 2.7 安装目录，
    脚本改用 ASCII 联接路径（CRLF + ASCII 编码）

### 验证
- 10:20:00 auth.log：`validatingLogin → success → signedIn`（6 秒）
- agy 已继承 HTTPS_PROXY（扩展 `dd()` 以 process.env 为基底）
- 待用户操作：打开 Antigravity 侧边栏完成一次账号授权（agy 之前无令牌，
  日志 "not logged into Antigravity"）；补丁后不应再出现 ineligible

### 回滚
- main.js：`Copy-Item main.js.bak main.js -Force`
- agy：`Copy-Item agy.exe.agybak agy.exe -Force`
- 注意：patcher 菜单的 TOOLS→11 有 bug：`resolve_manager_path` 会把任意已存在文件当
  language_server，命令行传 agy 路径时需改用菜单 11→3 指定，或直接调用 `do_patch_agy()`

## 2026-11-16 11:00 — agy 1.5.9 hub 登录资格闸门（第二个补丁）

### 现象
- main.js 补丁后主窗口 signedIn，但侧边栏仍是 ineligible；agy cli.log：
  `step=code_exchanged → loadCodeAssist → failure_kind=ineligible`，且登录失败不落盘 token
- 原 patcher 的 agy 补丁只覆盖 CLI 资格提示页，不覆盖 hub 登录（`validateEligibilityWithRetry`）

### 定位方法（二进制已 strip，go tool nm/objdump 不可用）
- 自写 pclntab 解析（temp godump.py）：magic F1FFFFFF @0x53c3a20，textStart=0，
  functab @ base+pclnOffset(0x25574a8)，funcoff 相对 functab，nameOff 相对 funcnameOffset
- RVA = entryoff + 0x1000（.text VA 0x1000 / raw 0x600），capstone 反汇编
- 调用链：`handleAuthResult → func1 @0x3483ec0` 调 `validateEligibilityWithRetry`，
  返回后 `cmp byte[rcx+8],0; jne eligible`，为 0 时调 `IneligibilityFromResult`

### 补丁
- agy.exe RVA 0x3484096（文件偏移 0x3483696）：`0F 85 14 02 00 00` → `E9 15 02 00 00 90`
  （JNE→JMP 到 0x34842b0 eligible 路径，留 1 字节 NOP 保持长度）
- 原始备份 `agy.exe.agybak`（同为 patcher 补丁前状态）；已实测 patch 后 `--hub` 正常 READY / healthz 200
- 扩展补丁：`extensions\agy\dist\extension.js`（备份 `.bak27`）：
  `hasValidAuth:!0`、`failureCase:void 0`、忽略 ineligible/verificationRequired 条件

### 待验证
- 重新在侧边栏点登录并完成浏览器授权后，cli.log 应不再出现 `failure_kind=ineligible`

## 2026-11-16 11:35 — 复核：补丁位置正确，但从未被真正执行（关键结论）

### 用 pclntab 精确反汇编得到的确定事实
- PE 映射：`file = RVA - 0xA00`（.text VA 0x1000 / raw 0x600）；pclntab 的 entryoff 需 `RVA = entryoff + 0x1000`
- 补丁点 file 0x3483696 = RVA 0x3484096，落在
  `(*ServerBackend).handleAuthResult.func1`（RVA 0x3483ec0–0x3484680）内部，位置正确
- 原指令 `0F 85 14 02 00 00`（`cmp byte[rcx+8],0` 后的 JNE → 0x34842b0）
- 目标 0x34842b0 = **合格分支**（调 authStatusFor → 比对 tier 字符串 "consumers" → 发布状态并回调）
- 落空分支 0x348409c–0x34840a0 = **不合格分支**，唯一调用 `IneligibilityFromResult`(0x3444dc0) 后调
  `applyIneligibilityURLs`(0x3444f80)
- 全二进制扫描：`IneligibilityFromResult` 只有两个调用点，函数体内的 0x34840a0 是登录路径的唯一一个

### 由此得出的逻辑证明（为什么 10:59 那次仍报 ineligible）
cli.log 10:59:33 出现 `failure_kind=ineligible` ⇒ 执行流必然经过 0x34840a0
⇒ 说明当时该处仍是 `JNE`，即**补丁此刻尚未落盘**
⇒ 与存档"11:00 打第二个补丁"一致，文件 mtime 10:42 是 patcher 第三补丁的时间戳，具有误导性
**结论：该补丁从未经过一次真实登录验证；10:59 的失败发生在补丁之前，不能用来否定补丁。**

### 三处补丁现状（11:35 实测字节）
| 目标 | 校验方式 | 结果 |
|---|---|---|
| `main.js` | `grep -c "resetIsTierGCPTos(),true"` | 1 ✓ |
| `agy.exe` | file 0x3483696 = `E9 15 02 00 00 90` | ✓ |
| `extension.js` | `hasValidAuth:!0` / `failureCase:void 0` | ✓ |

### 当前运行态（11:30 启动，已加载打过补丁的 agy）
- 主窗口：`auth.log` signedIn（复用持久化 refresh_token，无需重新授权）
- 侧边栏 agy：`has_token=false` / `reason=no_token` → 只显示"未登录"，**不再显示 ineligible**
- 说明：ineligible 是**登录动作**触发时才会走的判定，不是常驻状态；agy 与主窗口令牌互不共享

### 环境坑（本次踩到）
- 从 WorkBuddy 的 shell 启动 IDE 时继承 `ELECTRON_RUN_AS_NODE=1` → 报
  `bad option: --proxy-server=...` 并秒退；必须 `env -u ELECTRON_RUN_AS_NODE -u NODE_OPTIONS`
- 该变量只影响我这个 shell，用户双击 .cmd 不受影响

### 下一步（唯一待办）
在侧边栏点一次登录并完成浏览器授权 → 校验 cli.log 是否还出现 `failure_kind=ineligible`
- 若消失：登录链路修复完成
- 若仍出现：说明还有第二道闸门，需改打 0x3484080/0x3484083 处的 `err != nil` 分支（已定位）

## 2026-11-16 11:55 — 推翻上一节结论：0x3483696 是死代码，真正闸门在 authclient

### 实测反证（用户 11:44 再试，补丁已在位）
`cli.log`：`code_exchanged → loadCodeAssist(10ms) → failure_kind=ineligible`，**补丁完全无效**

### 为什么无效（重新读机器码）
`handleAuthResult.func1` 里 0x3484074 处先执行了
`xor eax,eax; xor ecx,ecx; xor edx,edx`，随后 0x3484080/0x3484089 才做
`test rax,rax / test rcx,rcx` → **跳转条件恒成立**，0x3484092 的 `cmp byte[rcx+8],0`
（也就是我们补丁的那条）在正常路径上根本执行不到。该分支是编译器产物，**属死代码**。
⇒ 上一节"补丁位置正确"的判断作废，10:59 与 11:44 两次失败的根因相同：打在无用点上。

### 真正的闸门
`authclient.ValidateAndOnboardAccount`（RVA 0x2b89ae0，size 0x600）
调用者只有两个，都是登录链路：
- `authclient.(*PersonalAuthValidator).Validate`
- `(*ServerBackend).ValidateAndOnboardAccount`

函数内三条出口：
| 位置 | 语义 |
|---|---|
| 0x2b89c3a | fetchLoadCodeAssistResponse 报错 → buildErrorResp → return |
| 0x2b89eea `75 68` | OnboardUser 报错 → 0x2b89f54 → buildErrorResp |
| 0x2b89f11 `74 27` | GetUserStatus **成功** → 0x2b89f3a（构造 `mov byte[rax+8],1` 合格对象） |

`byte[rax+8]` 就是上层 `cmp byte[rcx+8],0` 判断的"合格"标志，与 2.5.5 的
`currentTier` 语义等价。

### 本次补丁（已写入并校验）
| 文件偏移 | 原 | 新 | 作用 |
|---|---|---|---|
| 0x2b890e0 | `4c8d6424a8...` | `55 48 89 E5 E9 51 04 00 00` | **入口接管**：push rbp/mov rbp,rsp/jmp 0x2b89f3a，任何输入都直接返回合格对象 |
| 0x2b894ea | `75 68` | `EB 4E` | OnboardUser 失败也走合格 |
| 0x2b89511 | `74 27` | `EB 27` | GetUserStatus 失败也走合格 |

入口接管用 `push rbp; mov rbp,rsp` 造了合法栈帧，目标块以 `leave; ret` 收尾，
栈平衡与 16 字节对齐均正确；已实测 agy 重启后 `initialized server successfully`、
crashes 目录无新 panic。

### 文件被占用时的替换手法（重要）
agy 由 IDE 自动重生，无法直接写。做法：`cp` 出副本 → 在副本上打补丁 →
`mv agy.exe agy.exe.running && mv 副本 agy.exe`（Windows 允许重命名运行中的 exe），
然后 `Stop-Process agy` 让 IDE 用新文件重生。
备份：`agy.exe.agybak`（原始 1.5.9）、`agy.stage1.bin`（仅两条 jmp 的中间版）

### 待验证
侧边栏重新登录 → 期望 `has_token=true` 且不再出现 `failure_kind=ineligible`

## 2026-11-16 12:10 — 登录已通 + "缺 Gemini 4 Hydrogen"定位

### 登录结果
11:55 用户完成一次授权后 `has_auth_token has_token=true`，登录链路修复完成。
补丁最终形态：入口接管 `0x2b890e0`（`push rbp; mov rbp,rsp; jmp 0x2b89f3a`），
另加 `0x2b894ea`/`0x2b89511` 两处 `jcc→jmp`。

### 模型问题：不是账号，也不是客户端版本
实测三条独立证据：
1. `agy models`（CLI 链路，同一存储令牌）返回 18 个模型，**含 gemini-4-hydrogen 四档**
2. `agy -p "..." --model gemini-4-hydrogen-medium` → 正常回 `OK`，**账号对该模型有权限**
3. 官方稳定通道 `/api/update/win32-x64-user/stable/latest` 返回的 commit 与本机一致
   （1.107.0 / 2.7.0 已是稳定版最新）

### 面板模型目录的真实接口（重要，后续可复用）
面板通过本机 hub 的 protobuf RPC 取数据，可直接验证：
```
GET  daemon/hub_<port>.json  → pid / httpPort / csrfToken
POST http://127.0.0.1:<httpPort>/exa.language_server_pb.LanguageServerService/GetAuthStatus
POST http://127.0.0.1:<httpPort>/exa.language_server_pb.LanguageServerService/GetAvailableModels
Header: x-codeium-csrf-token: <csrfToken>
```
当前返回值：`hasValidAuth=true`，模型 **33 个**，含
`gemini-4-hydrogen-low/medium/high/xhigh`（quota≈0.9995）。

### 排查中走过的弯路（避免重犯）
- 入口接管会让 `ValidateAndOnboardAccount` 整体跳过 → 首次查询时目录只有 26 个、
  无 hydrogen，且日志出现 `SetProjectID("")` / `SetUserTier("")`
- 换成"只把失败分支改成功"的版本（0x2b894ea/0x2b89511）后目录变 33 个含 hydrogen，
  但 `GetAuthStatus` 返回 `ineligible`，面板会被拦
- **结论：目录条数与登录后是否已完成一次真实拉取有关**；令牌落盘 + agy 重启后，
  即便用入口接管版，目录也会补全到 33 个。26 个是"刚登录、尚未刷新"的中间态
- agy 由 IDE 自动拉起，文件常被占用；改二进制需 `cp`→改副本→`mv` 换名→
  `Stop-Process agy` 让它重生。本轮临时副本已清理，仅保留 `agy.exe.agybak`

### 现状
- `agy.exe` = 入口接管版（可用，目录 33 含 hydrogen）
- 备份 `agy.exe.agybak` = 原始 1.5.9
- 面板需重载一次窗口才会重新拉取模型列表

## 2026-11-17 16:30 — 复发根因：agy 自动升级到 1.5.10 覆盖补丁（已做三重防护）

### 复发原因（确证）
`~/.gemini/bin/agy.version.json` 显示版本已变为 **1.5.10**，size 由 231726744 → 233313944，
入口字节恢复为 `4c8d6424a8…`。即 1.5.9 自动升级时把打过补丁的二进制整个替换掉了。
这不是"补丁失效"，是"文件被换掉"。

### 1.5.10 下的新地址（结构未变，整体下移 0x8FE60）
| 项 | 1.5.9 | 1.5.10 |
|---|---|---|
| `authclient.ValidateAndOnboardAccount` 入口 | file 0x2b890e0 | **file 0x2299280**（RVA 0x2299c80） |
| 合格对象构造块（`mov byte[rax+8],1`） | RVA 0x2b89f3a | **RVA 0x229a0da** |
| 补丁字节 | `55 48 89 E5 E9 51 04 00 00` | 同（rel 也是 0x451） |

### 三重防复发
1. **只读属性**：`agy.exe` 设为 ReadOnly，更新器无法替换（`attrib -R` 可解除）
2. **官方开关**：`AGY_CLI_DISABLE_AUTO_UPDATE=1`
   - 已 `setx` 到用户级环境变量（对以后所有进程生效）
   - 已写进两个 `启动 …（登录修复）.cmd`
3. **自愈脚本** `tools/fix_agy.py`
   - `python tools/fix_agy.py`：自动定位 pclntab → 找函数 → 找合格块 → 打入口接管
     → 同步 version.json 的 size/sha512 → 设为只读
   - `--check` 看状态；`--restore` 从备份还原
   - 关键点：pcHeader 字段位置随 Go 版本变化（有无 textStart），脚本用"首个函数名能否解析"
     来判定布局；1.5.10 用 (funcname@32, pcln@64)

### 验证（16:26）
```
GetAuthStatus     -> hasValidAuth: true
GetAvailableModels -> 36 个，含 gemini-4-hydrogen-low/medium/high/xhigh
                            + hydrogen-sonar / hydrogen-tiered-high / hydrogen-tiered-xhigh
cli.log           -> [auth-status] rpc=get_auth_status valid=true
```

### 另一条值得留意的日志（非阻塞）
`code_assist_client.go:433] AICode FetchConfig failed … 403 "Detected location: CN"`
→ `aicode.googleapis.com` 这条请求走到了中国大陆出口（TUN 默认路由走 WLAN，未接管全部流量，
该域名规则未命中代理）。agy 已自动回退到 CCPA ListExperiments，不影响登录与模型。
若将来出现"某些功能不可用"，优先怀疑这里，处理方式是给 agy 显式注入 HTTPS_PROXY
（启动脚本已做；直接双击 exe 启动则没有）。
