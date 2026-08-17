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

## Install

```bash
claude plugin marketplace add HelloiOS2014/AgentRecovery
claude plugin install recover@agentrecovery --scope user
```

`--scope user` makes `/recover` available in every project.

## Usage

```
/recover                    # pick from the most recent sessions
/recover <session-id>       # recover a specific session
```

The picker pins sessions from the current project to the top (marked `*`,
with each session's original `cwd=` shown); `show` warns when the session
belongs to a different project than the current directory.

Session IDs: Codex CLI prints `codex resume <id>` on exit; the desktop app's
sessions also appear in the picker (by title).

## How it works

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

## Privacy / Security

Recovered tool output may contain secrets (API keys, configs). The handoff
is archived to `~/.claude/recover-handoffs/` with mode 600 (dir 0700), and
the render warns not to forward it. No content redaction is performed.

## Requirements & limits

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

Use in a Codex session: say "recover my Claude Code session", paste a Claude
Code session UUID, or run `/recover-claude`.

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
