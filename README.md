# AgentRecovery — `/recover` for Claude Code

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

## Development

```bash
python3 scripts/recover.py self-test   # fixture-based self-test (no framework)
```

Marketplace: `.claude-plugin/` manifests; local test with
`claude plugin marketplace add .`.
