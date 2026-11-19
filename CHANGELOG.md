# 变更记录

本仓库记录的是**修复工具本身**的演进。Antigravity / agy 的版本兼容性见文末矩阵。

---

## v1.1.0 — 2026-11-19

针对"重启后又被升级覆盖"的复发场景补齐兜底。

**新增**

- `tools/heal_agy.py` —— 自愈
  - `(size, mtime)` 缓存判重，文件没变时几乎零开销
  - 缺补丁则调用 `fix_agy.py` 重打，并恢复只读属性
  - 只有"本次刚打过补丁"才结束 agy 进程，让 IDE 用新二进制重生
- `tools/heal_watch.py` —— 盯梢
  - 拉起 IDE 后按间隔轮询多轮，覆盖"升级包还没下完"的时间窗
  - 配合启动器 / 登录自启使用，无需管理员权限
- `AGENT.md` 新增"防线 4：自愈守护"，并写明**禁止用 ACL 锁 agy.exe** 的原因

**修复**

- `tools/fix_agy.py`、`tools/verify_hub.py`：`tasklist` 输出改为显式 `decode("gbk","replace")`
  - 原写法 `text=True` 在 GBK 控制台会抛 `UnicodeDecodeError`，reader 线程崩溃后 stdout 变 `None`
  - 表现：`--restart` 阶段报 `AttributeError: 'NoneType' object has no attribute 'splitlines'`

**兼容性**

- agy 1.5.12：`ValidateAndOnboardAccount` 入口 RVA `0x21c4720`，合格块 `0x21c4b7a`，
  补丁字节仍是 `55 48 89 e5 e9 51 04 00 00`（rel=0x451），无需改代码

---

## v1.0.0 — 2026-11-17

首个公开版本，把 2026-09 至 2026-11 的完整排查成果沉淀为可复用工具包。

**新增**

- `tools/fix_agy.py` —— 核心补丁工具
  - 解析 Go `pclntab` 定位 `authclient.ValidateAndOnboardAccount`，**不写死文件偏移**
  - 自动判别 pcHeader 两种布局（有无 textStart），兼容不同 Go 版本
  - 在函数体内定位"合格块"（`lea rax,[type]` + `call newobject` + `mov byte[rax+8],1`）
  - 入口改写为 `push rbp; mov rbp,rsp; jmp <合格块>`
  - 自动备份、同步 `version.json` 的 size/sha512、设只读
  - 文件被运行中进程占用时自动降级为"改名换文件"，无需关闭 IDE
  - `--check` / `--dry-run` / `--restore` / `--restart`
- `tools/diagnose.py` —— 只读诊断，输出 8 种结论码，支持 `--json`
- `tools/verify_hub.py` —— 通过本地 hub RPC 验证 `hasValidAuth` 与模型目录
- `tools/launch_ide.py` —— 自动发现 IDE 路径与代理端口，注入代理变量后启动
- `tools/agydis.py` —— 反汇编辅助（按函数名/机器码/调用点检索）
- `scripts/launch-ide.cmd` —— 双击即用的启动器
- `docs/` —— 根因、日志对照、逆向方法、历史 main.js 补丁、原始归档

**关键结论（相较于早期判断的修正）**

- 早期认为补丁应打在 `(*ServerBackend).handleAuthResult.func1`，
  实测证明那段是**死代码**（寄存器已被 `xor` 清零，条件恒成立），从未生效
- 真正的闸门是 `authclient.ValidateAndOnboardAccount`，且有 4 个真实调用者，
  因此必须做**入口接管**而非分支修补
- "修好又坏"的唯一原因是 agy 静默自动升级覆盖了二进制，不是补丁失效

---

## 版本兼容矩阵

| 本工具 | agy | Antigravity IDE | 状态 |
|---|---|---|---|
| v1.0.0 | 1.5.9 | 2.7.0 (build 1.107.0) | ✅ 已验证（函数 file 0x2b890e0 / 合格块 RVA 0x2b89f3a） |
| v1.0.0 | 1.5.10 | 2.7.0 (build 1.107.0) | ✅ 已验证（函数 file 0x2299280 / 合格块 RVA 0x229a0da） |
| v1.0.0 | 1.5.x 其它 | 2.7.x | ⚠️ 未验证，理论上可自适应；失败请按 `docs/reverse-engineering.md` 定位 |
| v1.0.0 | < 1.5.9 | 2.7.x | ❌ 结构不同，需重新定位 |
| v1.0.0 | 任意 | ≤ 2.5.5 | ❌ 该版本无 agy 闸门，请改用 `docs/legacy-mainjs.md` |

**agy 升级后要做什么**

1. 先跑 `python tools/diagnose.py`
2. 若 `VERDICT: PATCH_MISSING` → `python tools/fix_agy.py --restart`
3. 若报"未找到函数/合格块" → 按 `docs/reverse-engineering.md` 手动定位，
   并把新地址提 issue 或在本地改 `fix_agy.py` 的特征（一般不需要）

---

## 已知限制

- 仅支持 Windows x64（补丁依赖 PE 节映射 `RVA - 0xA00`）
- 仅支持 agy 的 Go 二进制布局（magic `F1 FF FF FF`）
- 入口接管会让 `SetProjectID("")` / `SetUserTier("")` 为空，
  首次拉取的模型目录可能残缺；重启 agy + Reload Window 即补全
- 不解决账号层面的地区限制（本工具只是绕过客户端误判，不改变服务端授权）
