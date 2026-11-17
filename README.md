# Antigravity 登录修复工具包

> 解决 Antigravity IDE / Antigravity Hub 在**中国大陆网络 + 代理环境**下反复出现的
> "Sorry, this account is ineligible to use Antigravity" 登录失败。
>
> 适用于 Windows x64 + Antigravity IDE 2.7.x + agy CLI 1.5.x。

---

## 一、先看症状：是不是同一个问题

出现下面**任意一条**，本仓库适用：

| # | 现象 | 典型截图/文案 |
|---|---|---|
| 1 | 浏览器里 Google 授权成功，回到 IDE 侧边栏报账号不合格 | `Sorry, this account is ineligible to use Antigravity` |
| 2 | 主窗口能登录，但侧边栏（Agent 面板）一直"未登录"或报不合格 | 侧边栏与主窗口状态不一致 |
| 3 | 登录时卡住 / 超时 | `dial tcp 108.177.x.x:443: connect timeout`、`UND_ERR_CONNECT_TIMEOUT` |
| 4 | 昨天修好了，今天又坏了 | 通常是 `agy.exe` 自动升级把补丁覆盖了 |
| 5 | 登录成功但模型列表里没有 Gemini 4 Hydrogen | 面板拿到的是残缺模型目录 |

**不适用**：账号本身被封、Google 服务端 503 高负载、代理节点失效。
这三类在 [docs/log-signatures.md](docs/log-signatures.md) 里有区分方法。

---

## 二、快速修复（三步）

前提：**已经至少启动过一次 IDE 并点过一次登录**（让 `agy.exe` 自动下载到本地），
否则 `%USERPROFILE%\.gemini\bin\agy.exe` 不存在。

```bash
git clone https://github.com/e59292193/Antigravity-fix.git
cd Antigravity-fix
python tools\diagnose.py      # 1. 诊断，输出结论码
python tools\fix_agy.py       # 2. 按结论码修复（多数情况只需这一条）
python tools\verify_hub.py    # 3. 验证
```

修完后在 IDE 里 `Ctrl+Shift+P` → `Reload Window`，侧边栏重新点一次登录。

**用 AI Agent 修复**：直接把 [AGENT.md](AGENT.md) 丢给它，那是可执行的完整 runbook。

---

## 三、修复了什么（一句话版）

Antigravity 2.7 把登录资格判定从主进程（`main.js`）挪到了侧边栏后端 `agy.exe`。
`agy.exe` 拿到 `loadCodeAssist` 的成功响应后，仍会因为 `ineligibleTiers` 里的
`UNSUPPORTED_LOCATION`（免费层地区限制）判定"不合格"，而**无视账号已有的
`currentTier=standard-tier`（Google AI Pro 付费层）**，然后丢弃刚换到的 token。

本工具在 `agy.exe` 的 `authclient.ValidateAndOnboardAccount` 入口做一次
9 字节的跳转补丁，让校验函数直接返回"合格"对象，登录链路即可走完并落盘 token。

完整证据链见 [docs/root-cause.md](docs/root-cause.md)。

---

## 四、为什么需要"补丁"而不是改配置

排查过程中确认过、并**排除**的方向（别再重复踩）：

| 猜想 | 为什么不是 |
|---|---|
| 账号没资格 | 同一账号 `currentTier=standard-tier`、`paidTier=Google AI Pro`，另一台电脑可用 |
| 代理/节点问题 | 出口 IP 为美国，`loadCodeAssist` 10ms 内就返回了结果 |
| IDE 版本旧 | 官方稳定通道返回的 commit 与本机完全一致 |
| 清缓存 / 重装 / 删 Gemini Code Assist 连接 | 均无效，判定发生在服务端响应之后、客户端逻辑内 |
| 改 `jetski.cloudCodeUrl` 端点 | 只对主进程生效，管不到 `agy.exe` |

所以只能改客户端。这是**客户端判定 bug 的绕过**，不是账号修复。

---

## 五、目录结构

```
Antigravity-fix/
├── README.md                    ← 你在这里
├── AGENT.md                     ← 给 AI Agent 的完整执行手册（决策树 + 命令 + 期望输出）
├── CHANGELOG.md                 ← 版本演进与已知兼容性
├── tools/
│   ├── diagnose.py              ← 一键诊断，输出结论码
│   ├── fix_agy.py               ← 核心：版本自适应打补丁（含只读保护、备份、回滚）
│   ├── verify_hub.py            ← 通过本地 hub RPC 验证登录态与模型目录
│   ├── launch_ide.py            ← 带代理环境变量启动 IDE（自动发现安装路径）
│   └── agydis.py                ← 反汇编辅助：按 RVA 反汇编并标注函数名
├── scripts/
│   └── launch-ide.cmd           ← 双击即用的启动器（调用 launch_ide.py）
└── docs/
    ├── root-cause.md            ← 完整根因与证据链（三轮排查的结论）
    ├── log-signatures.md        ← 日志片段 → 结论 → 处理 对照表
    ├── reverse-engineering.md   ← 换版本后如何自己重新定位补丁点（pclntab 方法）
    ├── legacy-mainjs.md         ← 2.5.x 时代的 main.js 补丁（仅历史参考）
    └── progress-archive.md      ← 原始排查日志（逐日，含所有踩坑记录）
```

---

## 六、核心保证

- **可重复**：`fix_agy.py` 通过解析 Go 符号表定位函数，不写死文件偏移，agy 升级后仍可用
- **可回滚**：自动备份原厂文件，`--restore` 一键还原
- **防复发**：打补丁后设只读 + 写 `AGY_CLI_DISABLE_AUTO_UPDATE=1`，阻断自动升级覆盖
- **可验证**：`verify_hub.py` 直接问运行中的 agy 要真实登录态与模型列表，不靠猜 UI

已知副作用与限制见 [AGENT.md 附录](AGENT.md#附已知副作用与限制)。

---

## 七、免责声明

本项目修改的是本地已安装程序的可执行文件，用于绕过客户端一处**误判**。
它不会、也不能改变 Google 服务端对你账号的授权判定。

请仅在**你自己拥有合法订阅**的账号与设备上使用。因使用本工具产生的任何后果由使用者自行承担。
如果你不认可这种绕过方式，正确做法是向 Google 反馈该客户端 bug，或等待官方修复。
