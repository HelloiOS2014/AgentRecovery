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

var piUUID = regexp.MustCompile(`[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}`)

func defaultPiSessions() string {
	if v := os.Getenv("PI_AGENT_DIR"); v != "" {
		return filepath.Join(v, "sessions")
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".pi", "agent", "sessions")
}

func piUUIDFromName(name string) string {
	stem := name
	if strings.HasSuffix(name, ".jsonl") {
		stem = name[:len(name)-6]
	}
	return strings.ToLower(piUUID.FindString(stem))
}

type Pi struct {
	SessionsDir string
}

func NewPi(dir string) *Pi {
	if dir == "" {
		dir = defaultPiSessions()
	}
	return &Pi{SessionsDir: dir}
}

func (p *Pi) Name() string                  { return "pi" }
func (p *Pi) LoadTitles() map[string]string { return map[string]string{} }

func (p *Pi) sessionFiles() (map[string]string, error) {
	found := map[string]struct {
		path  string
		mtime int64
	}{}
	st, err := os.Stat(p.SessionsDir)
	if err != nil {
		if os.IsNotExist(err) {
			return map[string]string{}, nil
		}
		return nil, err
	}
	if !st.IsDir() {
		return map[string]string{}, nil
	}
	err = filepath.WalkDir(p.SessionsDir, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(d.Name(), ".jsonl") {
			return nil
		}
		sid := piUUIDFromName(d.Name())
		if sid == "" {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return nil
		}
		mt := info.ModTime().UnixNano()
		if old, ok := found[sid]; !ok || mt > old.mtime {
			found[sid] = struct {
				path  string
				mtime int64
			}{path, mt}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	out := map[string]string{}
	for sid, h := range found {
		out[sid] = h.path
	}
	return out, nil
}

func contentText(content any) (string, int) {
	if s, ok := content.(string); ok {
		return s, 0
	}
	nImg := 0
	var parts []string
	for _, b := range asList(content) {
		m := asMap(b)
		switch asStr(m["type"]) {
		case "text":
			parts = append(parts, asStr(m["text"]))
		case "image":
			nImg++
		}
	}
	return strings.Join(parts, "\n"), nImg
}

func (p *Pi) ListSessions(limit int) ([]core.SessionMeta, error) {
	files, err := p.sessionFiles()
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
	for _, x := range order {
		m := p.scanMeta(x.path, x.id)
		info, _ := os.Stat(x.path)
		m.UpdatedAt = isoSec(info.ModTime())
		metas = append(metas, m)
	}
	return metas, nil
}

func (p *Pi) scanMeta(path, sid string) core.SessionMeta {
	meta := core.SessionMeta{ID: sid}
	models := map[string]bool{}
	rec, _, err := readJSONL(path)
	if err != nil {
		return meta
	}
	for _, r := range rec {
		switch asStr(r["type"]) {
		case "session":
			if asStr(r["cwd"]) != "" {
				meta.Cwd = asStr(r["cwd"])
			}
			if asStr(r["timestamp"]) != "" {
				meta.StartedAt = asStr(r["timestamp"])
			}
			if asStr(r["id"]) != "" {
				meta.ID = asStr(r["id"])
			}
		case "session_info":
			if n := asStr(r["name"]); n != "" {
				meta.Title = n
			}
		case "model_change":
			if m := asStr(r["modelId"]); m != "" {
				models[m] = true
			}
		case "message":
			msg := asMap(r["message"])
			if m := asStr(msg["model"]); m != "" {
				models[m] = true
			}
			if meta.Title == "" && asStr(msg["role"]) == "user" {
				text, _ := contentText(msg["content"])
				text = strings.TrimSpace(text)
				if text != "" {
					meta.Title = firstLine(text, 80)
				}
			}
		}
	}
	if len(models) > 0 {
		meta.Model = strings.Join(sortedKeys(models), ", ")
	}
	return meta
}

func firstLine(s string, n int) string {
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

func (p *Pi) ReadSession(id string) (*core.Session, error) {
	want := strings.ToLower(id)
	files, err := p.sessionFiles()
	if err != nil {
		return nil, err
	}
	path := files[want]
	if path == "" {
		for sid, fp := range files {
			if strings.HasPrefix(sid, want) || strings.HasPrefix(want, sid) {
				path = fp
				break
			}
		}
	}
	if path == "" {
		return nil, fmt.Errorf("%w: 未找到 Pi 会话 %s（已扫描 %s）", ErrNotFound, id, p.SessionsDir)
	}
	return p.parse(path, id)
}

func (p *Pi) leafPath(records []map[string]any) []map[string]any {
	var tree []map[string]any
	for _, r := range records {
		if asStr(r["type"]) == "session" || asStr(r["id"]) == "" {
			continue
		}
		tree = append(tree, r)
	}
	if len(tree) == 0 {
		return nil
	}
	allNil := true
	for _, r := range tree {
		if asStr(r["parentId"]) != "" {
			allNil = false
			break
		}
	}
	if allNil {
		return tree
	}
	byID := map[string]map[string]any{}
	children := map[string]bool{}
	for _, r := range tree {
		id := asStr(r["id"])
		byID[id] = r
		if pid := asStr(r["parentId"]); pid != "" {
			children[pid] = true
		}
	}
	var leaves []map[string]any
	for _, r := range tree {
		if !children[asStr(r["id"])] {
			leaves = append(leaves, r)
		}
	}
	leaf := tree[len(tree)-1]
	if len(leaves) > 0 {
		leaf = leaves[0]
		for _, r := range leaves[1:] {
			if asStr(r["timestamp"]) > asStr(leaf["timestamp"]) {
				leaf = r
			}
		}
	}
	var path []map[string]any
	seen := map[string]bool{}
	cur := asStr(leaf["id"])
	for cur != "" && byID[cur] != nil && !seen[cur] {
		path = append(path, byID[cur])
		seen[cur] = true
		cur = asStr(byID[cur]["parentId"])
	}
	for i, j := 0, len(path)-1; i < j; i, j = i+1, j-1 {
		path[i], path[j] = path[j], path[i]
	}
	return path
}

func afterCompaction(path []map[string]any) (rest []map[string]any, tail []any, compacted bool, summary string) {
	last := -1
	for i, r := range path {
		if asStr(r["type"]) == "compaction" {
			last = i
		}
	}
	if last < 0 {
		return path, nil, false, ""
	}
	rec := path[last]
	summary = asStr(rec["summary"])
	if t, ok := rec["retainedTail"].([]any); ok {
		tail = t
	}
	for _, r := range path[last+1:] {
		if asStr(r["type"]) != "compaction" {
			rest = append(rest, r)
		}
	}
	return rest, tail, true, summary
}

func (p *Pi) parse(path, id string) (*core.Session, error) {
	meta := core.SessionMeta{ID: id, Source: "pi"}
	var events []core.Event
	calls := map[string]int{}
	var warnings []string
	models := map[string]bool{}
	unknown := map[string]int{}
	skippedImg := 0

	records, bad, err := readJSONL(path)
	if err != nil {
		return nil, err
	}
	for _, r := range records {
		if asStr(r["type"]) == "session" {
			meta.Cwd = asStr(r["cwd"])
			meta.StartedAt = asStr(r["timestamp"])
			if asStr(r["id"]) != "" {
				meta.ID = asStr(r["id"])
			}
			break
		}
	}
	pathRecs := p.leafPath(records)
	rest, retained, compacted, summary := afterCompaction(pathRecs)
	if compacted {
		warnings = append(warnings, "会话压缩过：已重置到压缩边界，仅保留摘要之后的上下文")
		if summary != "" {
			s := summary
			if len([]rune(s)) > 200 {
				s = string([]rune(s)[:200]) + "…"
			}
			warnings = append(warnings, "压缩摘要："+s)
		}
	}
	for _, msg := range retained {
		if m := asMap(msg); m != nil {
			skippedImg += handlePiMessage(m, &events, calls, &warnings, models)
		}
	}
	for _, r := range rest {
		switch asStr(r["type"]) {
		case "message":
			skippedImg += handlePiMessage(asMap(r["message"]), &events, calls, &warnings, models)
		case "session_info":
			if n := asStr(r["name"]); n != "" {
				meta.Title = n
			}
		case "model_change":
			if m := asStr(r["modelId"]); m != "" {
				models[m] = true
			}
		case "thinking_level_change", "custom", "custom_message", "label", "branch_summary", "compaction":
		default:
			t := asStr(r["type"])
			if t != "" {
				unknown[t]++
			}
		}
	}
	if meta.Title == "" {
		for _, e := range events {
			if e.Kind == "user_msg" && strings.TrimSpace(e.Text) != "" {
				meta.Title = firstLine(strings.TrimSpace(e.Text), 80)
				break
			}
		}
	}
	if len(models) > 0 {
		meta.Model = strings.Join(sortedKeys(models), ", ")
	}
	if bad > 0 {
		warnings = append(warnings, fmt.Sprintf("解析中跳过 %d 个坏行（并发写入或中断所致）", bad))
	}
	if skippedImg > 0 {
		warnings = append(warnings, fmt.Sprintf("跳过 %d 张图片（不恢复附件）", skippedImg))
	}
	keys := sortedKeys(func() map[string]bool {
		m := map[string]bool{}
		for t := range unknown {
			m[t] = true
		}
		return m
	}())
	for _, t := range keys {
		warnings = append(warnings, fmt.Sprintf("跳过未知记录类型 %s（%d 条）", t, unknown[t]))
	}
	return &core.Session{Meta: meta, Events: events, Compacted: compacted, Warnings: warnings}, nil
}

func handlePiMessage(msg map[string]any, events *[]core.Event, calls map[string]int, warnings *[]string, models map[string]bool) int {
	nImg := 0
	if m := asStr(msg["model"]); m != "" {
		models[m] = true
	}
	switch asStr(msg["role"]) {
	case "user":
		text, n := contentText(msg["content"])
		nImg += n
		if strings.TrimSpace(text) != "" {
			*events = append(*events, core.Event{Kind: "user_msg", Role: "user", Text: strings.TrimSpace(text)})
		}
	case "assistant":
		content := msg["content"]
		if list := asList(content); list != nil {
			for _, b := range list {
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
				case "toolCall":
					*events = append(*events, core.Event{Kind: "tool_call", Text: orQ(asStr(m["name"])), ToolArgs: jsonDump(m["arguments"])})
					if id := asStr(m["id"]); id != "" {
						calls[id] = len(*events) - 1
					}
				case "image":
					nImg++
				}
			}
		} else if s, ok := content.(string); ok && strings.TrimSpace(s) != "" {
			*events = append(*events, core.Event{Kind: "assistant_msg", Role: "assistant", Text: strings.TrimSpace(s)})
		}
	case "toolResult":
		text, n := contentText(msg["content"])
		nImg += n
		tid := asStr(msg["toolCallId"])
		if idx, ok := calls[tid]; ok {
			(*events)[idx].ToolOutput = text
		} else {
			*events = append(*events, core.Event{Kind: "tool_output", Text: text})
			*warnings = append(*warnings, "存在无法配对 toolCallId 的工具输出（已顺序追加）")
		}
	case "bashExecution":
		*events = append(*events, core.Event{
			Kind: "tool_call", Text: "bash",
			ToolArgs: asStr(msg["command"]), ToolOutput: asStr(msg["output"]),
		})
	}
	return nImg
}
