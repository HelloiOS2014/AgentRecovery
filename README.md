# AgentRecovery — cross-agent session recovery

Two independent plugins, one repo:

- **`recover`** (Claude Code marketplace, `.claude-plugin/`) — resume a
  **Codex** session inside Claude Code.
- **`recover-claude`** (Codex marketplace, `.agents/plugins/`) — recover a
  **Claude Code** session into Codex.

Each direction is a fully separate plugin with its own script tree; they only
share the render core (`scripts/core.py`, packed into the Codex plugin by
`scripts/pack-codex-plugin.sh`).

---

## Claude Code side: `/recover` (Codex → Claude Code)

Resume a **Codex** session inside Claude Code. When quota runs out mid-task
in Codex, switch here without losing context: `/recover` lists your recent
Codex sessions (or takes a session ID) and injects a budget-bounded hybrid
render of the conversation — recent turns verbatim within per-item caps
(the newest turn is always kept), older turns compressed — then you continue
the task.

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

Session IDs: Codex CLI prints `codex resume <id>` on exit; the desktop app's
sessions also appear in the picker (by title).

### How it works

- Reads local Codex session files (`~/.codex/sessions/**` and
  `~/.codex/archived_sessions/`) — no cloud calls, no Codex API.
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

## Codex side: `@recover-claude` (Claude Code → Codex)

Recover a **Claude Code** session into Codex. When Claude Code quota runs out
or you want to finish in Codex, the plugin lists your recent Claude Code
sessions (titles + `cwd=`, current-project sessions pinned on top) and
renders the picked one as a budget-bounded handoff that Codex continues from.

### Install

```bash
codex plugin marketplace add /path/to/this/repo   # local; or the git URL when published
codex plugin add recover-claude@agentrecovery
```

### Usage

In a Codex session (CLI or Desktop), trigger recovery any of these ways:

- 直接说：`恢复/继续 Claude Code 的会话`、`recover my Claude session`
- 粘贴一个 Claude Code 会话 UUID
- 输入 `/recover-claude`（或 `/recover`）——带参数时直接恢复指定会话：
  `/recover-claude <会话ID或序号>`

Codex 会运行脚本列出最近 20 个会话（当前项目的会话置顶，标 `*`，
`cwd=` 显示每个会话的原工作目录），等你选定（序号或完整 ID）后渲染 handoff，
然后从上次停下的地方继续任务。

会话 ID 从哪来：`~/.claude/projects/<项目目录>/*.jsonl` 的文件名，或 Claude
Code 里 `claude --resume` 的列表。

首次运行注意事项：
- 脚本读 `~/.claude/projects/`（工作区外），会触发权限确认——**必须批准**，
  否则退出码为 `2`（沙箱拦截），codex 会停止而不是瞎编会话
- 退出码含义：`0` = 正常列出（空列表也是真实结果）；`1` = 未检测到
  Claude Code（`~/.claude/projects` 不存在）；`2` = 沙箱/权限拦截
- 渲染完成后 handoff 存档在当前工作区 `.recover-handoff/<id>.md`

### How it works

- Reads local Claude Code session files (`~/.claude/projects/*/*.jsonl`) — no
  cloud calls. Honors `CLAUDE_CONFIG_DIR`.
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
- The render core (`scripts/core.py`) is shared with the Claude Code plugin;
  `scripts/pack-codex-plugin.sh` syncs the copy and runs both sides'
  self-tests. Run it after changing the core.

## Development

```bash
python3 scripts/recover.py self-test                          # Claude Code side
python3 hosts/codex/plugins/recover-claude/scripts/recover-claude.py self-test  # Codex side
./scripts/pack-codex-plugin.sh                                # sync core + double self-test
```

Marketplaces: `.claude-plugin/` (Claude Code) and `.agents/plugins/`
(Codex). Local test: `claude plugin marketplace add ./` (bare `.` rejected —
use `./` or an absolute path) and `codex plugin marketplace add <abs-path>`
(this codex version rejects `./` — use an absolute path).
