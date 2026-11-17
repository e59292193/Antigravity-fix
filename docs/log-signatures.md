# 日志特征对照表

拿到报错先在这张表里找对应行，再决定要不要动补丁。
所有日志路径以 `~` 表示 `%USERPROFILE%`。

---

## 一、去哪儿看

| 日志 | 路径 | 看什么 |
|---|---|---|
| agy 登录链路 | `~/.gemini/antigravity/cli.log` | **最关键**。`failure_kind`、`has_token`、`get_auth_status` |
| IDE 主进程认证 | `%APPDATA%\Antigravity IDE\logs\<最新会话>\auth.log` | `validatingLogin` → `signedIn` 是否走完 |
| IDE 主进程错误 | 同目录 `main.log` | `ERR_` 开头的窗口加载错误 |
| agy 崩溃 | `~/.gemini/antigravity/crashes/*.log` | 文件**非空**才说明真 panic |

```bash
# 最常用的一条
tail -40 ~/.gemini/antigravity/cli.log | grep -E "failure_kind|has_token|get_auth_status"
```

---

## 二、特征 → 结论 → 处理

### 2.1 登录资格类

| 日志特征 | 结论 | 处理 |
|---|---|---|
| `failure_kind=ineligible` | agy 资格闸门拦住了（L3） | `python tools/fix_agy.py --restart` |
| 同上，但补丁已在位 | 补丁未加载 或 版本结构变了 | 先确认 agy 进程启动时间晚于补丁时间；否则按 [reverse-engineering.md](reverse-engineering.md) 重定位 |
| `failure_kind=verificationRequired` | 账号需要二次验证 | 去浏览器完成 Google 验证，与本工具无关 |
| `failure_kind=` 其它非空值 | 看 `reason` 字段原文 | 按原文搜 |

### 2.2 网络类

| 日志特征 | 结论 | 处理 |
|---|---|---|
| `dial tcp <ip>:443: connect timeout` | agy 没走代理 | 用 `python tools/launch_ide.py` 启动 |
| `UND_ERR_CONNECT_TIMEOUT` | Electron 主进程 fetch 没走代理 | 同上（关键是 `NODE_USE_ENV_PROXY=1`） |
| `ERR_TIMED_OUT` + 窗口白屏 | 本地 UI 请求被送进代理 | 启动参数必须带 `--proxy-server=http://127.0.0.1:<port>` |
| `handshake failed; SSL error code 1, net_error -100` | 代理节点抖动 | 换节点，与本工具无关 |
| `Detected location: CN` + `aicode.googleapis.com` 403 | aicode 走了国内出口 | **非阻塞**，agy 会自动回退。介意的话用启动器启动 |

### 2.3 服务端 / 配额类

| 日志特征 | 结论 | 处理 |
|---|---|---|
| `UNAVAILABLE (code 503): No capacity available` | Google 端模型容量耗尽 | 换模型或等恢复，**不要**改补丁 |
| `RESOURCE_EXHAUSTED` / HTTP 429（全部模型） | 该账号在 prod 端点被限流 | 设 `jetski.cloudCodeUrl=https://daily-cloudcode-pa.googleapis.com` 换端点 |
| `remainingFraction: 0` | 配额用完 | 等重置 |
| `Our servers are experiencing high traffic` | 503 的 UI 文案 | 同上，非本地问题 |

### 2.4 状态确认类（正常时应看到）

| 日志特征 | 含义 |
|---|---|
| `[auth-login] ... step=code_exchanged` | OAuth 换码成功 |
| `has_auth_token has_token=true` | 令牌已落盘（**修复成功的标志**） |
| `[auth-status] get_auth_status valid=true` | agy 认为账号合格 |
| `initialized server successfully in <n>ms` | agy 启动正常 |
| `Wrote hub discovery ...` | 面板端口已就绪 |

### 2.5 反例：这些**不是**问题

| 日志特征 | 为什么不用管 |
|---|---|
| `crashes/*.log` 是**空文件** | 只有创建没有写入，不是 panic |
| `transcript.jsonl` 找不到（Mac 风格路径 `/Users/...`） | 已知路径 bug，只影响 transcript 写入 |
| `Detected location: CN` | 只影响 aicode 实验配置，已自动回退 |
| `SetProjectID("")` / `SetUserTier("")` | 入口接管的预期副作用，见下 |

---

## 三、入口接管的已知副作用

打完补丁后，`ValidateAndOnboardAccount` 被整体跳过，因此：

- `SetProjectID("")`、`SetUserTier("")` 会是**空字符串**（日志里可见）
- 刚登录完第一次查模型目录可能是残缺的（26 个、无 hydrogen）

**这不影响使用**：令牌落盘 + agy 重启 + Reload Window 后，模型目录会补全到
33/36 个并包含全部 hydrogen 变体。

判断是否真的出问题，只看两条：

```bash
python tools/verify_hub.py       # hasValidAuth=true 且 hydrogen 非空
agy models                       # CLI 侧也能列出 hydrogen
```

---

## 四、一分钟分诊脚本

```bash
python tools/diagnose.py          # 自动读上述日志并输出 VERDICT
```

它会把你需要看的字段一次性打印出来，省得手工 tail 五个文件。
