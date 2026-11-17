# 历史补丁：Antigravity 2.5.x 的 main.js 改动（仅作参考）

> **2.7.0 起已不再需要。** 2.7 把资格判定交给了侧边栏后端 `agy.exe`，
> 本目录下的 `fix_agy.py` 才是当前方案。
>
> 保留本文的原因：万一你在别的机器上装的是 2.5.x，症状同样是"账号不合格"，
> 那需要的是这里的改动。

---

## 一、适用版本

| IDE 版本 | 判定位置 | 需要本文？ |
|---|---|---|
| 2.5.5 及更早 | `resources/app/out/main.js` 的 `confirmUserForService` | 是 |
| 2.7.0+ | `~/.gemini/bin/agy.exe` 的 `ValidateAndOnboardAccount` | 否，用 `fix_agy.py` |

判断方法：

```bash
grep -c "confirmUserForService" \
  "<IDE安装目录>/resources/app/out/main.js"
```

- 命中且逻辑里有 `free-tier` 判定 → 2.5.x 结构
- 未命中 → 2.7+，别改 main.js

---

## 二、原代码与问题

`main.js` 里 `confirmUserForService` 的核心：

```js
const s = !!n || !i;
if (s) {
  await ...onboardUser("free-tier", t) ...
  // AUTH_SUCCESS
} else {
  // SET_INELIGIBLE  ->  "this account is ineligible"
}
```

- `n`：free-tier 是否可用
- `i`：是否有拒绝理由

对**付费账号**（`currentTier=standard-tier`），Google 仍会返回
`ineligibleTiers=[{tierId:"free-tier", UNSUPPORTED_LOCATION}]`。
于是 `n=false`、`i=true` → `s=false` → 判不合格，**完全忽略了账号已有正式层**。

这是纯客户端逻辑缺陷。

---

## 三、两处改动

### 改动 A：把"已有 currentTier"也算作合格

```diff
- const s = !!n || !i,
+ const s = !!n || !i || !!r?.currentTier,
```

### 改动 B：已有 currentTier 时跳过 free-tier 注册

```diff
- if (s) { await ...onboardUser("free-tier", t), ... }
+ if (s) { r?.currentTier || await ...onboardUser("free-tier", t), ... }
```

### 改动 C（可选）：`getProfileData` 容错

2.5.x 上曾在登录最后一步因为 `www.googleapis.com/oauth2/v2/userinfo`
TLS 握手被重置而卡在"There was an unexpected issue setting up your account"。

```diff
- profilePictureUrl: s.picture ?? "" } } catch (n) { throw this._logger.error(n), n } }
+ profilePictureUrl: s.picture ?? "" } } catch (n) { return this._logger.error(n),
+   { name: "", email: "", profilePictureUrl: "" } } }
```

> ⚠️ 改这段极易漏花括号导致主进程 `SyntaxError` 起不来。
> 改完务必先验证语法：`node --check`（对 `.mjs` 生效）或
> 用 `node -e "new Function(require('fs').readFileSync('main.js','utf8'))"` 粗检。

---

## 四、备份与回滚

```powershell
Copy-Item main.js main.js.bak -Force        # 改前备份
Copy-Item main.js.bak main.js -Force        # 回滚
```

---

## 五、为什么 2.7 之后失效

2.7 把登录拆成两条链路：

1. **主窗口**：仍走 `main.js`，但只负责 OAuth 与 `signedIn` 状态
2. **侧边栏（Agent 面板）**：走 `agy.exe --hub`，资格判定在 agy 内部

改 `main.js` 只能让**主窗口**显示已登录，侧边栏依旧报不合格。
实测：main.js 补丁在位时 `auth.log` 是 `signedIn`，同时
`cli.log` 是 `failure_kind=ineligible` —— 两个状态互不影响。

所以 2.7+ 请直接用 [AGENT.md](../AGENT.md) 的流程。
