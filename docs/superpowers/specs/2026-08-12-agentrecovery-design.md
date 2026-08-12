# AgentRecovery 设计文档

日期：2026-08-12 · 状态：已批准

## 背景与痛点

在 Codex 桌面端执行任务时，因额度或其他原因需要切换到其他 agent（如 Claude Code）继续，但切换导致上下文丢失。Grok Build 的做法是：根据 Codex 的 session ID 读取本地会话文件、整理上下文后继续。本项目为 Claude Code 实现同等能力：**在任意项目中，通过会话内技能 `/recover`，按 session ID 恢复 Codex 会话上下文并继续任务**。

## 关键决策（均已确认）

| 决策点 | 选择 | 理由 |
|---|---|---|
| 保真度 | **混合（C）**：最近 10 轮完整还原 + 更早历史确定性压缩 | 中途切任务的场景，最近现场（报错、半成品改动）最不能丢；全量倾倒污染上下文窗口 |
| 触发方式 | **会话内技能**（skill） | 切换时用户本来就在 Claude Code 窗口内，最自然；无额外进程 |
| 源抽象 | **可插拔源（B-lite）**：Source 接口 + Codex 首个实现 | 用户已有 agent-bridge 多 agent 生态，将来加 Grok/Antigravity 源时技能本体不动 |
| 语言 | Python stdlib | JSONL 多记录类型解析最顺手；零依赖零构建；社区参照实现（resume-skills）同为 Python，边界处理可直接借鉴 |
| 分发 | **Claude Code marketplace 插件** | 需要他人可安装；git 仓库即 marketplace |
| 仓库 | `HelloiOS2014/AgentRecovery`（公开） | 已创建 |

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
├── README.md                     # 分发说明
└── docs/superpowers/specs/       # 本 spec
```

### Source 接口（B-lite，约 20 行）

```python
class SessionMeta: id, title, cwd, started_at, updated_at
class Event: kind, role, text, tool_name, tool_args, tool_output, turn  # 归一化
class Session: meta, events: list[Event], compacted: bool

class Source:
    name: str
    def list_sessions(self, limit=20) -> list[SessionMeta]
    def read_session(self, session_id) -> Session
```

事件 kind：`user_msg / assistant_msg / reasoning / tool_call / tool_output`。

### CodexSource 实现要点（基于已验证事实）

- **发现**：先读 `~/.codex/session_index.jsonl` 拿标题/更新时间；扫 `~/.codex/sessions/**/rollout-<id>.jsonl` 定位文件（UUID 匹配文件名）
- **解析**：`session_meta`（cwd/模型/时间）· `response_item`（message / function_call / custom_tool_call / custom_tool_call_output / reasoning）· 跳过 `event_msg`/`turn_context`
- **加密**：reasoning 的 `encrypted_content` → 渲染为 `[思维链已加密，跳过]`
- **压缩**：`compacted` 记录 → 头部标注「该会话已压缩，工具细节不可用」（压缩后文件只剩消息骨架）
- 本机 1197 个会话全部明文 JSONL，无 zstd；SQLite 变体记入已知边界，不实现

## 数据流（/recover 流程）

```
用户敲 /recover [id]
├─ 无 id → recover.py list            # 最近 20 个会话：标题/日期/cwd，用户选序号或粘贴 ID
└─ 有 id → recover.py show <id> --recent 10
     渲染输出（stdout + 存档 ~/.claude/recover-handoffs/<id>.md）：
     1. 头部：源/标题/ID/起止时间/cwd/模型/压缩标记
     2. 历史区（10 轮之前）：每轮 = 用户请求（~200 字截断）+ 助手回复首段（~400 字）+ 工具名列表
     3. 最近区（最后 10 轮）：全文保真；工具参数原文、单条输出 ~2k 字上限；加密 reasoning 标注
     4. 文件改动清单：从 write/apply_patch 类工具参数提取路径，去重
     5. 结尾指令行：继续任务；核对 cwd 是否与当前目录一致
     注入对话后，技能指示模型：向用户总结恢复内容 → 核对 cwd → 继续任务
```

轮 = 以用户消息为起点的一段完成周期，窗口按轮数切。脚本只做确定性渲染，综合判断交给模型。

## 技能内脚本定位（不复制、不装全局）

SKILL.md 内一条 find（grok-bridge 已验证模式）：

```bash
RECOVER_PY="$(find "$HOME/.claude/plugins" -path "*/agentrecovery/*" -name recover.py 2>/dev/null | head -n1)"
python3 "$RECOVER_PY" ...
```

缓存路径规律：`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`。

## 分发

```bash
claude plugin marketplace add HelloiOS2014/AgentRecovery
claude plugin install recover@agentrecovery --scope user
```

`--scope user` 使技能在所有项目可用。本地开发用 `claude plugin marketplace add .`（本地目录）测试。

## 错误处理

| 场景 | 行为 |
|---|---|
| session ID 不存在 | 清晰报错 + 提示获取 ID 的途径（`codex resume` 列表 / 桌面端复制） |
| 压缩会话 | 头部标注降级「工具细节不可用」 |
| Codex 并发写入（读到时半行 JSON） | 跳过坏行 + 警告 |
| 插件文件缺失 | 错误信息直接给出安装命令 |
| cwd 与当前目录不符 | 头部显示原 cwd，技能指示模型提醒用户 |

## 测试

`recover.py --self-test`：内嵌真实形态 fixture（消息/工具调用/加密 reasoning/compacted 标记/多 session_meta），断言：ID 定位、事件顺序、加密跳过、窗口切分（10 轮）、压缩标注。单文件、无框架、`assert` 即可。

## 已知边界（不实现）

- Grok/Antigravity 等其他源：接口已留，后续按需新增解析器文件
- Codex SQLite 会话变体 / zstd 压缩：本机数据无此形态
- 反向恢复（Claude Code 会话 → 其他 agent）：不在本需求范围
- 只恢复上下文，不恢复进程态（非 live restore）
