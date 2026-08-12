# AgentRecovery 设计文档

日期：2026-08-12 · 状态：已修订（经独立设计审查 + 真实数据验证）

## 背景与痛点

在 Codex 桌面端执行任务时，因额度或其他原因需要切换到其他 agent（如 Claude Code）继续，但切换导致上下文丢失。Grok Build 的做法是：根据 Codex 的 session ID 读取本地会话文件、整理上下文后继续。本项目为 Claude Code 实现同等能力：**在任意项目中，通过会话内技能 `/recover`，按 session ID 恢复 Codex 会话上下文并继续任务**。

## 关键决策（均已确认）

| 决策点 | 选择 | 理由 |
|---|---|---|
| 保真度 | **混合**：最近 N 轮现场保真（逐项上限内）+ 更早历史确定性压缩 | 中途切任务的场景，最近现场（报错、半成品改动）最不能丢；全量倾倒污染上下文窗口 |
| 触发方式 | **会话内技能**（skill） | 切换时用户本来就在 Claude Code 窗口内，最自然；无额外进程 |
| 源抽象 | **可插拔源（B-lite）**：Source 接口 + Codex 首个实现 | 用户已有 agent-bridge 多 agent 生态，将来加 Grok/Antigravity 源时技能本体不动 |
| 语言 | Python stdlib | JSONL 多记录类型解析最顺手；零依赖零构建；社区参照实现（resume-skills）同为 Python |
| 分发 | **Claude Code marketplace 插件** | 需要他人可安装；git 仓库即 marketplace |
| 仓库 | `HelloiOS2014/AgentRecovery`（公开，已创建） | — |

## 架构

```
AgentRecovery/                    # git 仓库，即 marketplace 本体
├── .claude-plugin/
│   ├── marketplace.json          # name=agentrecovery, owner, plugins: [{name: recover, source: github}]
│   └── plugin.json               # name=recover, version=0.1.0, author
├── skills/recover/SKILL.md       # /recover 技能
├── scripts/
│   ├── recover.py                # 主 CLI：list / show / self-test（纯 stdlib）
│   └── sources/
│       ├── __init__.py           # 注册表 SOURCES = {"codex": CodexSource}
│       └── codex.py              # CodexSource：发现 + 解析 + 渲染
├── README.md                     # 分发说明 + 权限提示 + 平台边界
└── docs/superpowers/specs/       # 本 spec
```

### Source 接口（B-lite）

```python
class SessionMeta: id, title, cwd, started_at, updated_at   # updated_at = 最后活动时间
class Event: kind, role, text, tool_name, tool_args, tool_output, turn  # 归一化
class Session: meta, events: list[Event], compacted: bool

class Source:
    name: str
    def list_sessions(self, limit=20) -> list[SessionMeta]
    def read_session(self, session_id) -> Session
```

事件 kind：`user_msg / assistant_msg / reasoning / tool_call / tool_output`。

## CodexSource 实现要点（全部基于真实数据验证）

### 发现（文件扫描为主，index 仅作标题补充）

1. 扫 `~/.codex/sessions/**/rollout-*.jsonl`（2026 起按 `YYYY/MM/DD` 分目录）+ `~/.codex/sessions/rollout-*.json`（2025 时代，位于 sessions 根目录）+ `~/.codex/archived_sessions/rollout-*.jsonl`（桌面端归档区，实测 380 条 index 中有 18 条只有归档副本）
2. **ID 提取**：文件名尾部的 UUID（`[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$` 正则），文件名形如 `rollout-<时间戳>-<UUID>.jsonl`
3. **排序与去重**：列表按最后活动时间（文件 mtime / 末条记录时间）倒序；`session_index.jsonl` 仅用于标题查找，按 ID 去重、取最新 `updated_at` 的标题（实测存在同一 ID 多条记录）；无标题条目显示「无标题（cwd，时间）」
4. 列表条目若任何位置都找不到文件（实测 0.5%），标记「不可用」而非报错

### 解析

- `session_meta`：cwd/模型/时间；**多记录时取最后一条**（压缩/重启会产生多条）
- `response_item`：
  - message：**只渲染 `role=user` / `role=assistant`**；`role=developer` 整体丢弃（含 base_instructions 人格提示词、`<app-context>` 等，实测单文件 90/314 是 developer 消息，是最大噪声源）
  - **成块注入包装剥离**（实测验证）：`<environment_context>…</environment_context>`（内含 cwd/shell/date/timezone/workspace_roots 全部随块消失，cwd 已在头部展示）与 `<recommended_plugins>…</recommended_plugins>`（实测用户消息以该块开头）。**实现规格**：正则需 `re.S`（DOTALL）且 `re.sub` 全局替换——实测 env 块为多行（含 `<cwd>/<shell>/<current_date>` 子标签，单文件 2.5k chars），单行正则只剥掉开标签、把注入内容原样漏给模型。**行内注解保留**：`<path>`、`<redacted>`、`<key>` 等是 Redact/转录内容的一部分，`<redacted>` 标记了密钥位置，不得剥离——只剥有闭合标签的**已知**包装块（allowlist），不按标签名一刀切（denylist 会毁掉用户自写的成对 XML 与 `<redacted>…</redacted>` 位置标记）；allowlist 失败安全：未知标签表现为噪声而非数据丢失。标签列表作为具名常量 + 代码注释指向 Codex 结构
  - function_call / custom_tool_call：名 + 参数（参数有字符上限，见预算）
  - function_call_output / custom_tool_call_output：输出（字符上限 + 截断标记）
  - reasoning：`summary_text` 有明文则渲染（上限 100 字符）；否则 `[思维链已加密，跳过]`
- **配对规则**：tool_output 按 `call_id` 匹配 tool_call（`custom_tool_call` 有 call_id/status）；匹配不到时按追加日志顺序归位（单线程追加日志实测交错干净）
- 跳过 `event_msg`/`turn_context`/`world_state`：对话正文在 response_item 中完整可得；**明确取舍**：event_msg 里的图片/附件引用（images/local_images）不恢复——已声明，可接受
- `compacted` 记录 → 头部标注「该会话已压缩，工具细节不可用」（压缩后文件只剩消息骨架）
- **中断尾部**：最后一条是 tool_call 而无对应 output（额度耗尽被杀正是此形态）→ 渲染「调用了 X（无输出，可能被中断）」
- 并发写入的坏行（半行 JSON）→ 跳过 + 警告

## 上下文预算（硬性边界，常量已按真实数据校准）

目标：单次渲染 ≤ ~60k chars（≈15k tokens），最坏情况不爆窗口。**校准依据（全部实测，会话 019ff01c，394 条记录）**：① 初版上限渲染 ≈147k chars（≈36k tokens，其中 ~10k 为 repr 双重转义虚增，真实 ~134k）——初版常量不成立；② v3 上限下全会话 9 轮 ≈97k chars；③ **最新一轮（38 次工具调用）独占 60,355 chars = 40k 最近区预算的 151%**——本轮即为「额度耗尽被杀」的目标形态，直接决定最近区地板规则（见下）。逐项上限：

| 项 | 上限 |
|---|---|
| 用户消息 | 1000 chars |
| 助手消息 | 1500 chars |
| 工具参数 | 600 chars |
| 工具输出 | 1200 chars |
| reasoning 摘要 | 100 chars |

- **最近区**：预算 40k chars（软上限）。裁剪规则：逐项上限后从最旧整轮丢弃直至预算内，**但永远保留最近一轮，即使超预算**——实测校准会话的最新一轮（38 次工具调用的巨轮，正是「额度耗尽被杀」的目标形态）达 60,355 chars，若无地板则全部丢光 = 空渲染，这是本工具存在的意义，必须保留。**N=10 是上限而非承诺**：实测密集会话保 0-1 轮（最新轮超预算时）/ 常规会话 3-7 轮
- **历史区**：预算 20k chars；每轮 = 用户请求（200 截断）+ 助手首段（400）+ 工具名列表；总条目上限 50 轮，**预算与轮数双重约束，超任一即丢最旧**（实测 ~1k chars/轮，20k 预算在 ~20 轮处先触顶，50 轮上限实际不可达）
- 「轮」= 以用户消息为起点的一段完成周期（Codex 在两条用户消息间可能自主跑 20+ 工具调用，单轮内容不可控，故预算按轮内总量封顶，而非按轮计数）
- 实测会话跨天追加（同一文件可含 07-28→08-10 多天记录）：窗口按最后 N 轮切分即可，头部显示起止日期
- **截断标记统一**：所有被逐项上限截断的内容（用户/助手消息、工具参数、工具输出、reasoning 摘要）一律以「…(截断)」收尾——用户自己贴的报错信息被截断时模型必须可见
- **空轮**：整条用户消息剥完包装块后为空（实测存在，turn 3 全为包装块）→ 渲染为「[空]」而非留白，避免模型误读
- 渲染末尾附截断统计（丢了几轮/几条超限/文件清单截断数），让模型知道信息完整性边界

## 数据流（/recover 流程）

```
用户敲 /recover [id]
├─ 无 id → recover.py list            # 最近 20 个会话：标题/最后活动时间/cwd，用户选序号或粘贴 ID
└─ 有 id → recover.py show <id> --recent 10
     渲染输出（stdout + 存档 ~/.claude/recover-handoffs/<id>.md）：
     1. 头部：源/标题/ID/起止时间/cwd/模型/压缩标记
     2. 历史区（10 轮之前）：每轮 = 用户请求（200）+ 助手首段（400）+ 工具名列表
     3. 最近区（最后 ≤10 轮）：逐项上限内现场保真；超预算丢最旧轮但永远保留最近一轮（软上限）
     4. 文件改动清单：从 write/apply_patch 类工具参数提取路径，去重，上限 40 条 + "+N 更多"
     5. 结尾指令行：继续任务；核对 cwd；提示工作区可能有未提交改动
     6. 截断统计（footer）：丢了几轮/几条超限、文件清单截断数——裁剪结果只在渲染末尾可知
     注入对话后，技能指示模型：向用户总结恢复内容 → 核对 cwd → 继续任务
```

脚本只做确定性渲染，综合判断交给模型。

## 技能内脚本定位（版本安全）

```bash
RECOVER_PY="$(find "$HOME/.claude/plugins" -path "*agentrecovery/*" -name recover.py 2>/dev/null \
  | grep '/cache/' \
  | sort -V | tail -n1)"
```

要点（实测 `~/.claude/plugins/cache/agent-bridge-claude/antigravity-bridge/` 堆积了 16 个版本目录，`head -n1` 会取到最旧版本——插件更新后用户会永久跑旧代码）：
- 只取 `cache/` 下的副本（`marketplaces/` 目录有重复的源码副本，不参与选择）
- 按版本号 `sort -V` 取最大（路径含 `<marketplace>/<plugin>/<version>/` 段）
- 找不到 → 报错并给出安装命令

## 分发

```bash
claude plugin marketplace add HelloiOS2014/AgentRecovery
claude plugin install recover@agentrecovery --scope user
```

`--scope user` 使技能在所有项目可用。本地开发用 `claude plugin marketplace add .`（本地目录）测试。
README 必须注明：首次 `/recover` 会触发 find/python3 的权限确认（建议预先放行）；平台边界为 macOS/Linux（`python3`），Windows 不支持。

## 安全

- 存档目录 `~/.claude/recover-handoffs/` `mkdir -p` 后 `chmod 700`，文件 `chmod 600`（工具输出可能含密钥/配置）
- 渲染头部加一行「此文件包含工具输出，可能含密钥；请勿外发」
- 不做内容脱敏（过度工程，明确不实现）

## 错误处理

| 场景 | 行为 |
|---|---|
| session ID 不存在（含归档/旧格式） | 报错并列出已扫描的目录与匹配规则 |
| 压缩会话 | 头部标注降级「工具细节不可用」 |
| 中断尾部（tool_call 无 output） | 渲染「调用了 X（无输出，可能被中断）」 |
| 并发写入坏行 | 跳过 + 警告 |
| 插件脚本缺失 | 错误信息直接给出安装命令 |
| cwd 与当前目录不符 | 头部显示原 cwd，技能指示模型提醒用户 |

## 测试

`recover.py --self-test`，内嵌真实形态 fixture，断言：
- ID 定位（尾部 UUID 正则，含 sessions/ 根目录 .json 与 archived_sessions 形态）
- 事件顺序与 call_id 配对（含配对失败回退顺序归位）
- developer 消息丢弃、environment_context 剥离（**含多行 fixture**）、recommended_plugins 剥离、行内 `<redacted>` 保留、reasoning 摘要明文渲染/加密跳过
- 窗口切分与预算封顶（超预算丢最旧轮、**最近一轮软上限保留**——用 38 调用巨轮 fixture 复现 019ff01c 实测形态）
- 空轮「[空]」标记、统一「…(截断)」标记
- 中断尾部标注、compacted 标注、多 session_meta 取最后一条
- 列表去重（重复 ID）、无标题行、不可用标记
- 坏行跳过

## 已知边界（明确不实现）

- Grok/Antigravity 等其他源：接口已留，后续按需新增解析器文件
- Codex SQLite 会话变体 / zstd 压缩：本机 1197 个会话全部明文 JSONL，无需 zstd；SQLite 变体记入已知边界
- 图片/附件恢复（event_msg images/local_images）：丢弃
- 内容脱敏：不做，仅权限与提示
- 存档清理：不自动清理，用户可自行删除目录
- 反向恢复（Claude Code 会话 → 其他 agent）：不在本需求范围
- 只恢复上下文，不恢复进程态（非 live restore）
- Windows 平台：不支持（依赖 python3）
- 包装块 allowlist 的残余风险：Codex 新增注入包装标签时，该标签按「噪声」渲染（失败安全，非数据丢失），直到常量扩展；代码中以具名常量 + 注释指向 Codex 结构，升级 Codex 后运行 self-test 即可发现

## 修订记录

- 2026-08-12 v2：合并独立设计审查（critical ×3 / important ×5 / minor ×6）。采纳：文件扫描为主索引、上下文预算硬边界、版本安全脚本定位、role 过滤与 environment_context 剥离、存档权限、配对规则、中断尾部渲染、多 session_meta 取最后、.json/.jsonl/归档三形态扫描。否决：审查员关于 session_index 已过时（2026-04-20）的论断——实测文件 mtime 与内容均更新至 2026-08-11、桌面会话在册；但「文件扫描为主」的修法仍因标题缺失/归档形态而采纳。补充（本会话独立验证）：archived_sessions 目录形态、index 重复 ID、2025 时代 .json 文件。
- 2026-08-12 v3：冒烟测试驱动的校准（真实数据运行）。① 预算常量：真实会话按初版上限渲染实测 147k chars，初版目标 30k 不成立——逐项上限下调（助手 2k→1.5k、参数 800→600、输出 2k→1.2k、摘要 200→100），总量目标调至 ~60k chars（≈15k tokens），最近区 40k / 历史区 20k，N=10 明示为上限而非承诺，渲染末尾附截断统计。② 噪声剥离精确化：实测用户消息含 `<recommended_plugins>` 成块包装（与 environment_context 一样可剥），而行内注解 `<path>/<redacted>/<key>` 等是转录内容必须保留——规则改为「只剥有闭合标签的已知包装块」。③ 尾部形态抽样：200 个会话末尾均为 message/reasoning，无「tool_call 无 output」结尾——中断尾部渲染保留为防御，常见形态不触发。
- 2026-08-12 v4：增量设计审查（delta 审查 v2/v3 修订，量化测量）。致命修复：**最近区裁剪无地板 → 空渲染**——实测最新一轮（38 调用）60.4k chars 超出 40k 预算 151%，严格裁剪全丢；加「永远保留最近一轮（软上限）」规则，「3-7 轮」修正为实测「0-1 轮（密集）/ 3-7 轮（常规）」。其余：reasoning 上限 200/100 矛盾修正为 100、截断统计移出头部仅留 footer、文件改动清单上限 40 条、剥离正则规格化（DOTALL/全局/allowlist 失败安全）、校准数字补充 v3 实测（全会话 97k / 最新轮 60.4k）、历史区双重约束明示、空轮「[空]」标记、截断标记统一。
