package sources

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/HelloiOS2014/AgentRecovery/internal/core"
)

var claudeUUID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

var noiseTypes = map[string]bool{
	"last-prompt": true, "mode": true, "permission-mode": true,
	"file-history-snapshot": true, "file-history-delta": true, "queue-operation": true,
}

var skipFlags = []string{"isMeta", "isCompactSummary", "isVirtual", "isVisibleInTranscriptOnly"}

func defaultClaudeProjects() string {
	if v := os.Getenv("CLAUDE_CONFIG_DIR"); v != "" {
		return filepath.Join(v, "projects")
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".claude", "projects")
}

type Claude struct {
	ProjectsDir string
}

func NewClaude(dir string) *Claude {
	if dir == "" {
		dir = defaultClaudeProjects()
	}
	return &Claude{ProjectsDir: dir}
}

func (c *Claude) Name() string                  { return "claude" }
func (c *Claude) LoadTitles() map[string]string { return map[string]string{} }

func (c *Claude) sessionFiles() (map[string]string, error) {
	found := map[string]string{}
	st, err := os.Stat(c.ProjectsDir)
	if err != nil {
		if os.IsNotExist(err) {
			return found, nil
		}
		return nil, err
	}
	if !st.IsDir() {
		return found, nil
	}
	entries, err := os.ReadDir(c.ProjectsDir)
	if err != nil {
		return nil, err
	}
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		sub := filepath.Join(c.ProjectsDir, e.Name())
		files, err := os.ReadDir(sub)
		if err != nil {
			continue
		}
		for _, f := range files {
			name := f.Name()
			if f.IsDir() || !strings.HasSuffix(name, ".jsonl") {
				continue
			}
			sid := name[:len(name)-6]
			if claudeUUID.MatchString(sid) {
				found[sid] = filepath.Join(sub, name)
			}
		}
	}
	return found, nil
}

func (c *Claude) ListSessions(limit int) ([]core.SessionMeta, error) {
	files, err := c.sessionFiles()
	if err != nil {
		return nil, err
	}
	type pair struct {
		id, path string
		mtime    int64
	}
	var order []pair
	for sid, path := range files {
		info, err := os.Stat(path)
		if err != nil {
			continue
		}
		order = append(order, pair{sid, path, info.ModTime().UnixNano()})
	}
	sort.Slice(order, func(i, j int) bool { return order[i].mtime > order[j].mtime })
	if limit > 0 && len(order) > limit {
		order = order[:limit]
	}
	var metas []core.SessionMeta
	for _, p := range order {
		title, cwd, started, models := c.scanMeta(p.path)
		model := strings.Join(sortedKeys(models), ", ")
		info, _ := os.Stat(p.path)
		metas = append(metas, core.SessionMeta{
			ID: p.id, Title: title, Cwd: cwd, StartedAt: started,
			UpdatedAt: isoSec(info.ModTime()), Model: model,
		})
	}
	return metas, nil
}

func sortedKeys(m map[string]bool) []string {
	var k []string
	for s := range m {
		k = append(k, s)
	}
	sort.Strings(k)
	return k
}

func (c *Claude) scanMeta(path string) (title, cwd, started string, models map[string]bool) {
	models = map[string]bool{}
	rec, _, err := readJSONL(path)
	if err != nil {
		return
	}
	for _, r := range rec {
		switch asStr(r["type"]) {
		case "ai-title":
			if t := asStr(r["aiTitle"]); t != "" {
				title = t
			}
		case "user", "assistant":
			if cwd == "" {
				cwd = asStr(r["cwd"])
			}
			if started == "" {
				started = asStr(r["timestamp"])
			}
			if m := asStr(asMap(r["message"])["model"]); m != "" {
				models[m] = true
			}
		}
	}
	return
}

func (c *Claude) ReadSession(id string) (*core.Session, error) {
	files, err := c.sessionFiles()
	if err != nil {
		return nil, err
	}
	path := files[id]
	if path == "" {
		return nil, fmt.Errorf("%w: 未找到 Claude Code 会话 %s（已扫描 %s 下所有项目目录）", ErrNotFound, id, c.ProjectsDir)
	}
	return c.parse(path, id)
}

func skippedFlag(r map[string]any) bool {
	for _, f := range skipFlags {
		if asBool(r[f]) {
			return true
		}
	}
	return false
}

func (c *Claude) parse(path, id string) (*core.Session, error) {
	meta := core.SessionMeta{ID: id}
	var events []core.Event
	calls := map[string]int{}
	compacted := false
	var warnings []string
	models := map[string]bool{}
	unknown := map[string]int{}
	skippedAtt, skippedFlags, skippedLocal := 0, 0, 0

	rec, bad, err := readJSONL(path)
	if err != nil {
		return nil, err
	}
	for _, r := range rec {
		if skippedFlag(r) {
			skippedFlags++
			continue
		}
		t := asStr(r["type"])
		switch t {
		case "user":
			if meta.Cwd == "" {
				meta.Cwd = asStr(r["cwd"])
			}
			if meta.StartedAt == "" {
				meta.StartedAt = asStr(r["timestamp"])
			}
			skippedLocal += handleClaudeUser(r, &events, calls, &warnings)
		case "assistant":
			if meta.Cwd == "" {
				meta.Cwd = asStr(r["cwd"])
			}
			if meta.StartedAt == "" {
				meta.StartedAt = asStr(r["timestamp"])
			}
			handleClaudeAssistant(r, &events, calls, models)
		case "ai-title":
			if t := asStr(r["aiTitle"]); t != "" {
				meta.Title = t
			}
		case "system":
			if asStr(r["subtype"]) == "compact_boundary" {
				events, calls = nil, map[string]int{}
				compacted = true
				ts := asStr(r["timestamp"])
				if len(ts) > 16 {
					ts = ts[:16]
				}
				if ts == "" {
					ts = "?"
				}
				warnings = append(warnings, "会话在 "+ts+" 压缩过：已重置到压缩边界，仅保留摘要之后的上下文")
			}
		case "attachment":
			skippedAtt++
		default:
			if noiseTypes[t] {
				break
			}
			if t != "" {
				unknown[t]++
			}
		}
	}
	if bad > 0 {
		warnings = append(warnings, fmt.Sprintf("解析中跳过 %d 个坏行（并发写入或中断所致）", bad))
	}
	if skippedFlags > 0 {
		warnings = append(warnings, fmt.Sprintf("跳过 %d 条元数据/摘要记录（isMeta/isCompactSummary 等）", skippedFlags))
	}
	if skippedLocal > 0 {
		warnings = append(warnings, fmt.Sprintf("跳过 %d 条本地命令回显（/compact 等）", skippedLocal))
	}
	if skippedAtt > 0 {
		warnings = append(warnings, fmt.Sprintf("跳过 %d 条 attachment 记录（hook、token 用量等）", skippedAtt))
	}
	for t, n := range unknown {
		warnings = append(warnings, fmt.Sprintf("跳过未知记录类型 %s（%d 条）", t, n))
	}
	if len(models) > 0 {
		meta.Model = strings.Join(sortedKeys(models), ", ")
	}
	return &core.Session{Meta: meta, Events: events, Compacted: compacted, Warnings: warnings}, nil
}

func handleClaudeUser(r map[string]any, events *[]core.Event, calls map[string]int, warnings *[]string) int {
	content := asMap(r["message"])["content"]
	if s, ok := content.(string); ok {
		text := strings.TrimSpace(s)
		if text == "" {
			return 0
		}
		if strings.HasPrefix(text, "<command-name>") || strings.HasPrefix(text, "<local-command-") {
			return 1
		}
		*events = append(*events, core.Event{Kind: "user_msg", Role: "user", Text: text})
		return 0
	}
	list := asList(content)
	var texts []string
	var results []map[string]any
	for _, b := range list {
		m := asMap(b)
		switch asStr(m["type"]) {
		case "text":
			if t := asStr(m["text"]); t != "" {
				texts = append(texts, t)
			}
		case "tool_result":
			results = append(results, m)
		}
	}
	if len(texts) > 0 {
		*events = append(*events, core.Event{Kind: "user_msg", Role: "user", Text: strings.TrimSpace(strings.Join(texts, "\n"))})
	}
	for _, b := range results {
		attachToolResult(b, events, calls, warnings)
	}
	return 0
}

func attachToolResult(b map[string]any, events *[]core.Event, calls map[string]int, warnings *[]string) {
	tid := asStr(b["tool_use_id"])
	content := b["content"]
	var text string
	switch c := content.(type) {
	case string:
		text = c
	case []any:
		var parts []string
		for _, x := range c {
			if t := asStr(asMap(x)["text"]); t != "" {
				parts = append(parts, t)
			}
		}
		text = strings.Join(parts, "\n")
	}
	if strings.HasPrefix(text, "<persisted-output>") {
		text = "<persisted-output>…（完整输出在 ~/.claude 的 tool-results 目录）"
		*warnings = append(*warnings, "存在持久化输出 stub，工具输出未内联")
	}
	if tid != "" {
		if idx, ok := calls[tid]; ok {
			(*events)[idx].ToolOutput = text
			return
		}
	}
	*events = append(*events, core.Event{Kind: "tool_output", Text: text})
	*warnings = append(*warnings, "存在无法配对 tool_use_id 的工具输出（已顺序追加）")
}

func handleClaudeAssistant(r map[string]any, events *[]core.Event, calls map[string]int, models map[string]bool) {
	msg := asMap(r["message"])
	if m := asStr(msg["model"]); m != "" {
		models[m] = true
	}
	for _, b := range asList(msg["content"]) {
		m := asMap(b)
		switch asStr(m["type"]) {
		case "thinking":
			th := strings.TrimSpace(asStr(m["thinking"]))
			if th != "" {
				*events = append(*events, core.Event{Kind: "reasoning", Role: "assistant", Text: th})
			}
		case "text":
			t := strings.TrimSpace(asStr(m["text"]))
			if t != "" {
				*events = append(*events, core.Event{Kind: "assistant_msg", Role: "assistant", Text: t})
			}
		case "tool_use":
			*events = append(*events, core.Event{
				Kind: "tool_call", Text: orQ(asStr(m["name"])), ToolArgs: jsonDump(m["input"]),
			})
			if id := asStr(m["id"]); id != "" {
				calls[id] = len(*events) - 1
			}
		}
	}
}

func orQ(s string) string {
	if s == "" {
		return "?"
	}
	return s
}
