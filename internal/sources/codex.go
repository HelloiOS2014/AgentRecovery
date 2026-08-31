package sources

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/HelloiOS2014/AgentRecovery/internal/core"
)

var uuidEnd = regexp.MustCompile(`[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

var wrapperRes = []*regexp.Regexp{
	regexp.MustCompile(`(?s)<environment_context>.*?</environment_context>`),
	regexp.MustCompile(`(?s)<recommended_plugins>.*?</recommended_plugins>`),
}

func stripWrappers(text string) string {
	for _, re := range wrapperRes {
		text = re.ReplaceAllString(text, "")
	}
	return text
}

func uuidFromFilename(name string) string {
	stem := name
	if strings.HasSuffix(name, ".jsonl") {
		stem = name[:len(name)-6]
	} else if strings.HasSuffix(name, ".json") {
		stem = name[:len(name)-5]
	}
	return uuidEnd.FindString(stem)
}

type Codex struct {
	Dirs      []string
	IndexPath string
}

func NewCodex(dirs []string, indexPath string) *Codex {
	home, _ := os.UserHomeDir()
	if dirs == nil {
		dirs = []string{
			filepath.Join(home, ".codex", "sessions"),
			filepath.Join(home, ".codex", "archived_sessions"),
		}
	}
	if indexPath == "" {
		indexPath = filepath.Join(home, ".codex", "session_index.jsonl")
	}
	return &Codex{Dirs: dirs, IndexPath: indexPath}
}

func (c *Codex) Name() string { return "codex" }

func (c *Codex) LoadTitles() map[string]string {
	best := map[string]string{}
	rec, _, err := readJSONL(c.IndexPath)
	if err != nil {
		return best
	}
	for _, d := range rec {
		sid, title := asStr(d["id"]), asStr(d["thread_name"])
		if sid != "" && title != "" {
			best[sid] = title
		}
	}
	return best
}

func (c *Codex) findFile(id string) string {
	var found string
	for _, base := range c.Dirs {
		filepath.WalkDir(base, func(path string, d os.DirEntry, err error) error {
			if err != nil || d.IsDir() || found != "" {
				return nil
			}
			name := d.Name()
			if strings.HasPrefix(name, "rollout-") && uuidFromFilename(name) == id {
				found = path
			}
			return nil
		})
		if found != "" {
			return found
		}
	}
	return ""
}

func firstCwd(path string) string {
	rec, _, err := readJSONL(path)
	if err != nil {
		return ""
	}
	for _, d := range rec {
		if asStr(d["type"]) == "session_meta" {
			return asStr(asMap(d["payload"])["cwd"])
		}
	}
	return ""
}

func isoSec(mtime time.Time) string {
	return mtime.Format("2006-01-02T15:04:05")
}

func (c *Codex) ListSessions(limit int) ([]core.SessionMeta, error) {
	type hit struct {
		path  string
		mtime time.Time
	}
	found := map[string]hit{}
	for _, base := range c.Dirs {
		if st, err := os.Stat(base); err != nil || !st.IsDir() {
			continue
		}
		err := filepath.WalkDir(base, func(path string, d os.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if d.IsDir() {
				return nil
			}
			name := d.Name()
			if !strings.HasPrefix(name, "rollout-") {
				return nil
			}
			if !strings.HasSuffix(name, ".jsonl") && !strings.HasSuffix(name, ".json") {
				return nil
			}
			sid := uuidFromFilename(name)
			if sid == "" {
				return nil
			}
			info, err := d.Info()
			if err != nil {
				return nil
			}
			if old, ok := found[sid]; !ok || info.ModTime().After(old.mtime) {
				found[sid] = hit{path: path, mtime: info.ModTime()}
			}
			return nil
		})
		if err != nil && !os.IsNotExist(err) {
			return nil, err
		}
	}
	titles := c.LoadTitles()
	var metas []core.SessionMeta
	for sid, h := range found {
		metas = append(metas, core.SessionMeta{
			ID:        sid,
			Title:     titles[sid],
			UpdatedAt: isoSec(h.mtime),
		})
	}
	sort.Slice(metas, func(i, j int) bool { return metas[i].UpdatedAt > metas[j].UpdatedAt })
	if limit > 0 && len(metas) > limit {
		metas = metas[:limit]
	}
	for i := range metas {
		metas[i].Cwd = firstCwd(found[metas[i].ID].path)
	}
	return metas, nil
}

func (c *Codex) ReadSession(id string) (*core.Session, error) {
	path := c.findFile(id)
	if path == "" {
		d0, d1 := "?", "?"
		if len(c.Dirs) > 0 {
			d0 = c.Dirs[0]
		}
		if len(c.Dirs) > 1 {
			d1 = c.Dirs[1]
		}
		return nil, fmt.Errorf("%w: 未找到会话 %s：已扫描 %s 与 %s（含归档与 2025 旧格式）", ErrNotFound, id, d0, d1)
	}
	return c.parse(path, id)
}

func (c *Codex) parse(path, id string) (*core.Session, error) {
	meta := core.SessionMeta{ID: id}
	var events []core.Event
	calls := map[string]int{}
	compacted := false
	var warnings []string
	rec, bad, err := readJSONL(path)
	if err != nil {
		return nil, err
	}
	for _, d := range rec {
		switch asStr(d["type"]) {
		case "session_meta":
			p := asMap(d["payload"])
			model := asStr(p["model"])
			if model == "" {
				model = asStr(p["model_provider"])
			}
			meta = core.SessionMeta{ID: id, Cwd: asStr(p["cwd"]), StartedAt: asStr(p["timestamp"]), Model: model}
		case "compacted":
			compacted = true
		case "response_item":
			handleCodexItem(asMap(d["payload"]), &events, calls, &warnings)
		}
	}
	if bad > 0 {
		warnings = append(warnings, fmt.Sprintf("解析中跳过 %d 个坏行（并发写入或中断所致）", bad))
	}
	return &core.Session{Meta: meta, Events: events, Compacted: compacted, Warnings: warnings}, nil
}

func handleCodexItem(it map[string]any, events *[]core.Event, calls map[string]int, warnings *[]string) {
	k := asStr(it["type"])
	switch k {
	case "message":
		role := asStr(it["role"])
		if role != "user" && role != "assistant" {
			return
		}
		var b strings.Builder
		for _, c := range asList(it["content"]) {
			m := asMap(c)
			if asStr(m["text"]) != "" {
				b.WriteString(asStr(m["text"]))
			}
		}
		text := b.String()
		if role == "user" {
			text = stripWrappers(text)
		}
		kind := "assistant_msg"
		if role == "user" {
			kind = "user_msg"
		}
		*events = append(*events, core.Event{Kind: kind, Role: role, Text: text})
	case "function_call", "custom_tool_call":
		args := it["arguments"]
		if args == nil {
			args = it["input"]
		}
		argStr := jsonDump(args)
		*events = append(*events, core.Event{Kind: "tool_call", Text: asStr(it["name"]), ToolArgs: argStr})
		if cid := asStr(it["call_id"]); cid != "" {
			calls[cid] = len(*events) - 1
		}
	case "function_call_output", "custom_tool_call_output":
		out := it["output"]
		if out == nil {
			out = it["content"]
		}
		outStr := jsonDump(out)
		if idx, ok := calls[asStr(it["call_id"])]; ok {
			(*events)[idx].ToolOutput = outStr
		} else {
			*events = append(*events, core.Event{Kind: "tool_output", Text: outStr})
			*warnings = append(*warnings, "存在无法配对 call_id 的工具输出（已顺序追加）")
		}
	case "reasoning":
		sum := asStr(it["summary_text"])
		if sum == "" {
			sum = "[思维链已加密，跳过]"
		}
		*events = append(*events, core.Event{Kind: "reasoning", Text: sum})
	}
}
