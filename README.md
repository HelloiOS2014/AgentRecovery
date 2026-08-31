# AgentRecovery — cross-agent session recovery

One repo, three hosts:

- **Claude Code** (`.claude-plugin/`) — `/recover` resumes a **Codex or Pi**
  session; `/recover-self` resumes an earlier Claude Code session.
- **Codex** (`.agents/plugins/` → `hosts/codex/`) — `/recover` resumes a
  **Claude Code or Pi** session; `/recover-self` resumes an earlier Codex
  session.
- **Pi** (`hosts/pi/package/`) — native `/recover` and `/recover-self`
  (TUI picker + injected handoff, not a skill).

`/recover` always targets **other** agents, `/recover-self` targets the
**same** agent. All three share the render core and parsers
(`scripts/core.py` + `scripts/sources/`, packed by
`scripts/pack-codex-plugin.sh`).

---

## Same-agent recovery（同 agent 会话延续）

`/recover-self` 是同 agent 恢复。两个 agent **原生**就有无损恢复，优先用它们：

```bash
codex resume               # Codex: picker；codex resume <uuid> 任意项目；--last 续最近
claude --continue          # Claude Code: 续最近会话；--resume 当前项目 picker
pi -c / pi -r / /resume    # Pi: 续最近；-r 与 /resume 只列当前项目
```

原生 resume 恢复完整会话文件。`/recover-self` 在两种场景下补充原生能力：

- **跨项目恢复**：codex 的 resume 支持任意项目，claude 的 `--resume` 只列当前
  项目——picker 显示 `cwd=` 并从任意目录按会话 ID 恢复
- **会话整理 / 手记**：不想完整重载上下文时，把长会话渲染成预算受限的
  handoff（最近轮次逐项保真 + 历史压缩 + 文件清单 + 截断统计），开新会话
  带着手记延续，省 token

picker 单源（recover 只列对方、recover-self 只列自己），当前项目置顶标 `*`；
完整 session ID 跨源自动识别，粘贴时不用指定来源。

---

## Claude Code side: `/recover` + `/recover-self`

Resume a **Codex or Pi** session (`/recover`) or an earlier Claude Code session
(`/recover-self`) inside Claude Code. When quota runs out mid-task in another
agent, or you want to start fresh without rebuilding context: the matching
command lists the relevant sessions (or takes a session ID) and injects a
budget-bounded hybrid render of the conversation — recent turns verbatim
within per-item caps (the newest turn is always kept), older turns compressed
— then you continue the task.

### Install

```bash
claude plugin marketplace add HelloiOS2014/AgentRecovery
claude plugin install recover@agentrecovery --scope user
```

`--scope user` makes `/recover` available in every project.

### Usage

```
/recover                    # pick from the most recent sessions
/recover <session-id>       # recover a specific session
```

The picker pins sessions from the current project to the top (marked `*`,
with each session's original `cwd=` shown); `show` warns when the session
belongs to a different project than the current directory.

Session IDs: Codex CLI prints `codex resume <id>` on exit; Claude Code
session IDs are the `~/.claude/projects/<项目>/*.jsonl` filenames. A full ID is
auto-detected across both stores.

### How it works

- Reads local Codex (`~/.codex/sessions/**`, `~/.codex/archived_sessions/`)
  and Pi (`~/.pi/agent/sessions/`) session files — no cloud calls.
- Parser: keeps user/assistant messages and tool calls (paired by `call_id`);
  strips Codex-injected wrapper blocks (`<environment_context>`,
  `<recommended_plugins>`); keeps inline annotations like `<redacted>`;
  skips encrypted chain-of-thought.
- Render budget ~60k chars: caps per item (user 1000 / assistant 1500 /
  tool args 600 / tool output 1200 / reasoning 100), recent-zone 40k with
  oldest-first trimming but the newest turn is always kept, history zone 20k.
  A truncation-stats footer tells you what detail was dropped.
- Recovery is deterministic: the script renders; the model continues.

### Privacy / Security

Recovered tool output may contain secrets (API keys, configs). The handoff
is archived to `~/.claude/recover-handoffs/` with mode 600 (dir 0700), and
the render warns not to forward it. No content redaction is performed.

### Requirements & limits

- macOS / Linux with `python3` (stdlib only, no dependencies).
- Codex sessions must be local and plain JSONL (the default; no zstd).
- Windows is not supported.
- Recovers context, not process state; images/attachments are not restored.
- Compacted Codex sessions render without tool-call detail (flagged in the header).
- First `/recover` run triggers permission confirmations for `find` and
  `python3` — pre-allow them in Claude Code settings to avoid mid-flow stalls.

---

## Codex side: `/recover` + `/recover-self`

Recover a **Claude Code or Pi** session (`/recover`) or an earlier **Codex**
session (`/recover-self`) into Codex. When quota runs out in Claude Code, you
want to finish here, or you want a fresh Codex session to continue an older
one, the plugin lists the relevant sessions (titles + `cwd=`, current-project
sessions pinned on top) and renders the picked one as a budget-bounded handoff
that Codex continues from.

### Install

```bash
codex plugin marketplace add /path/to/this/repo   # local; or the git URL when published
codex plugin add recover@agentrecovery
```

### Usage

In a Codex session (CLI or Desktop), trigger recovery any of these ways:

- `/recover` → 恢复 Claude Code 会话；`/recover-self` → 恢复 Codex 自己的会话
- 直接说：`恢复/继续 Claude Code 的会话`、`继续我之前的 Codex 会话`
- 粘贴一个会话 UUID（自动识别来源）

Codex 会运行脚本列出最近 20 个会话（当前项目的会话置顶标 `*`、
`cwd=` 显示原工作目录），等你选定（序号或完整 ID，跨源自动识别）后渲染
handoff，然后从上次停下的地方继续任务。

会话 ID 从哪来：Codex 退出时打印的 `codex resume <id>`，或
`~/.claude/projects/<项目目录>/*.jsonl` 的文件名（Claude Code 侧）。

首次运行注意事项：
- 脚本读 `~/.claude/projects/`（recover）或 `~/.codex/sessions/`
  （recover-self，工作区外），会触发权限确认——**必须批准**，否则退出码为
  `2`（沙箱拦截），codex 会停止而不是瞎编会话
- 退出码含义：`0` = 正常列出（空列表也是真实结果）；`1` = 目标存储无会话；
  `2` = 沙箱/权限拦截
- 渲染完成后 handoff 存档在当前工作区 `.recover-handoff/<id>.md`

### How it works

- Reads local session files (`~/.claude/projects/*/*.jsonl` for /recover,
  `~/.codex/sessions/**` for /recover-self) — no cloud calls. Honors
  `CLAUDE_CONFIG_DIR`.
- Parser is Claude-specific: streams an assistant message across several
  records (thinking / text / tool_use) back into one event sequence, pairs
  `tool_result` blocks by `tool_use_id` without opening fake user turns,
  resets at the last `compact_boundary` (pre-compact history stays in the
  file and would otherwise double the transcript), and skips
  `isMeta`/`isCompactSummary`/sidechain/attachment records with counts in the
  warnings footer.
- Exit codes are meaningful: `0` listed (empty is a real empty result),
  `1` no Claude Code detected, `2` sandbox/permission blocked reading
  `~/.claude` — never confuse blocked with empty.
- The handoff is archived to `.recover-handoff/<id>.md` **inside the
  workspace** (sandbox-writable), not `~/.claude` (outside the sandbox).

### Notes

- Runs `python3` — in Codex Desktop, `python3` must be on the non-interactive
  PATH. First run triggers permission approvals; the skill stops cleanly if
  they are denied (never uses sandbox bypass flags).
- The render core (`scripts/core.py`) is shared with the Claude Code plugin
  and the Pi package; `scripts/pack-codex-plugin.sh` syncs the copies and
  runs every host's self-test. Run it after changing the core.

## Pi side: `/recover` + `/recover-self`

Native Pi commands (extension, not a skill). `/recover` lists Claude Code
and Codex sessions; `/recover-self` lists earlier Pi sessions as a
budget-bounded handoff. Use `/resume` / `pi -r` when you want a lossless
reload of the current project.

### Install

```bash
pi install git:github.com/HelloiOS2014/AgentRecovery
```

Requires `python3` on PATH (stdlib only).

### Usage

- `/recover` — pick a Claude Code or Codex session (TUI), inject handoff, continue
- `/recover-self` — same for an earlier Pi session (any project)
- `/recover <session-id>` — skip the picker

The picker pins the current project (`*`) and tags each row `[claude]` /
`[codex]`. Handoff is archived to `.recover-handoff/<id>.md` in the workspace.

## Development

```bash
python3 scripts/recover.py self-test                          # Claude Code side
python3 hosts/codex/plugins/recover/scripts/recover.py self-test          # Codex side
python3 hosts/pi/package/scripts/recover.py self-test                     # Pi side
./scripts/pack-codex-plugin.sh                                # sync core + all self-tests
```

Marketplaces: `.claude-plugin/` (Claude Code) and `.agents/plugins/`
(Codex). Pi uses `pi install`. Local test: `claude plugin marketplace add ./`
(bare `.` rejected — use `./` or an absolute path) and
`codex plugin marketplace add <abs-path>` (this codex version rejects `./` —
use an absolute path).
