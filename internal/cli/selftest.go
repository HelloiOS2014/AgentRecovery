package cli

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/HelloiOS2014/AgentRecovery/internal/core"
	"github.com/HelloiOS2014/AgentRecovery/internal/sources"
)

func runSelfTest() int {
	var fail []string
	check := func(name string, cond bool) {
		if cond {
			fmt.Println("PASS " + name)
		} else {
			fmt.Println("FAIL " + name)
			fail = append(fail, name)
		}
	}
	tmp, err := os.MkdirTemp("", "ar-go-self-test-")
	if err != nil {
		fmt.Println("FAIL tempdir")
		return 1
	}

	// --- Codex ---
	os.MkdirAll(filepath.Join(tmp, "sessions", "2026", "08", "11"), 0o755)
	os.MkdirAll(filepath.Join(tmp, "sessions", "2025"), 0o755)
	os.MkdirAll(filepath.Join(tmp, "archived_sessions"), 0o755)
	sidModern := "01234567-89ab-cdef-0123-456789abcdef"
	sidLegacy := "fedcba98-7654-3210-fedc-ba9876543210"
	sidArch := "11111111-2222-3333-4444-555555555555"
	modern := filepath.Join(tmp, "sessions", "2026", "08", "11", "rollout-2026-08-11T17-17-17-"+sidModern+".jsonl")
	legacy := filepath.Join(tmp, "sessions", "2025", "rollout-2025-06-27-"+sidLegacy+".json")
	arch := filepath.Join(tmp, "archived_sessions", "rollout-2026-03-04T20-05-38-"+sidArch+".jsonl")
	os.WriteFile(modern, []byte(`{"type":"session_meta","payload":{"cwd":"/x","timestamp":"2026-08-11T10:00:00Z","model_provider":"openai"}}`+"\n"), 0o644)
	os.WriteFile(legacy, []byte(`{"type":"session_meta","payload":{"cwd":"/y"}}`+"\n"), 0o644)
	os.WriteFile(arch, []byte(`{"type":"session_meta","payload":{"cwd":"/z"}}`+"\n"), 0o644)
	setMtime(modern, "2026-08-11T17:17:17")
	setMtime(legacy, "2025-06-27T12:00:00")
	setMtime(arch, "2026-03-04T20:05:38")

	codex := sources.NewCodex([]string{filepath.Join(tmp, "sessions"), filepath.Join(tmp, "archived_sessions")}, filepath.Join(tmp, "session_index.jsonl"))
	metas, _ := codex.ListSessions(20)
	ids := map[string]bool{}
	for _, m := range metas {
		ids[m.ID] = true
	}
	check("list finds modern jsonl", ids[sidModern])
	check("list finds legacy root .json", ids[sidLegacy])
	check("list finds archived jsonl", ids[sidArch])
	check("list sorted by updated_at desc", len(metas) > 0 && metas[0].ID == sidModern)
	cwdBy := map[string]string{}
	for _, m := range metas {
		cwdBy[m.ID] = m.Cwd
	}
	check("list fills cwd from first session_meta", cwdBy[sidModern] == "/x" && cwdBy[sidLegacy] == "/y" && cwdBy[sidArch] == "/z")

	idx := filepath.Join(tmp, "session_index.jsonl")
	os.WriteFile(idx, []byte(
		`{"id":"`+sidModern+`","thread_name":"旧标题"}`+"\n"+
			`{"id":"`+sidModern+`","thread_name":"分析近7天用户反馈"}`+"\n"+
			`{"id":"`+sidLegacy+`","thread_name":"2025老会话"}`+"\n",
	), 0o644)
	codex.IndexPath = idx
	metas, _ = codex.ListSessions(20)
	byID := map[string]core.SessionMeta{}
	for _, m := range metas {
		byID[m.ID] = m
	}
	check("title from index, last occurrence wins", byID[sidModern].Title == "分析近7天用户反馈")
	check("title lookup works", byID[sidLegacy].Title == "2025老会话")

	userText := "<recommended_plugins>Here is a list of plugins…</recommended_plugins>\n帮我分析一下这个报错\n<environment_context>\n<cwd>/Users/x</cwd>\n</environment_context>"
	var b strings.Builder
	jl := func(v any) {
		raw, _ := json.Marshal(v)
		b.Write(raw)
		b.WriteByte('\n')
	}
	jl(map[string]any{"type": "session_meta", "payload": map[string]any{"cwd": "/x", "timestamp": "2026-08-11T10:00:00Z", "model_provider": "openai"}})
	jl(map[string]any{"type": "response_item", "payload": map[string]any{"type": "message", "role": "developer", "content": []any{map[string]any{"type": "input_text", "text": "dev"}}}})
	jl(map[string]any{"type": "response_item", "payload": map[string]any{"type": "message", "role": "user", "content": []any{map[string]any{"type": "input_text", "text": userText}}}})
	jl(map[string]any{"type": "response_item", "payload": map[string]any{"type": "message", "role": "assistant", "content": []any{map[string]any{"type": "output_text", "text": "我先看看这个报错"}}}})
	jl(map[string]any{"type": "response_item", "payload": map[string]any{"type": "reasoning", "encrypted_content": "gAAAAA"}})
	jl(map[string]any{"type": "response_item", "payload": map[string]any{"type": "reasoning", "summary_text": "用户想看报错分析"}})
	jl(map[string]any{"type": "response_item", "payload": map[string]any{"type": "custom_tool_call", "name": "apply_patch", "input": map[string]any{"file_path": "/x/src/a.py"}, "call_id": "call_1"}})
	jl(map[string]any{"type": "response_item", "payload": map[string]any{"type": "custom_tool_call_output", "call_id": "call_1", "output": map[string]any{"status": "success"}}})
	jl(map[string]any{"type": "response_item", "payload": map[string]any{"type": "function_call", "name": "shell", "arguments": `{"cmd":"pwd"}`, "call_id": "call_2"}})
	jl(map[string]any{"type": "response_item", "payload": map[string]any{"type": "function_call_output", "call_id": "call_9", "output": "orphan output"}})
	b.WriteString("not-json-line\n")
	jl(map[string]any{"type": "response_item", "payload": map[string]any{"type": "message", "role": "assistant", "content": []any{map[string]any{"type": "output_text", "text": "改完了"}}}})
	jl(map[string]any{"type": "compacted", "payload": map[string]any{}})
	jl(map[string]any{"type": "session_meta", "payload": map[string]any{"cwd": "/x-new", "timestamp": "2026-08-11T11:00:00Z"}})
	os.WriteFile(modern, []byte(b.String()), 0o644)

	sess, err := codex.ReadSession(sidModern)
	check("read modern", err == nil && sess != nil)
	if sess != nil {
		check("compacted flag set", sess.Compacted)
		check("bad line counted in warnings", hasSub(sess.Warnings, "坏行"))
		check("last session_meta wins", sess.Meta.Cwd == "/x-new")
		check("wrapper blocks stripped from user message", sess.Events[0].Kind == "user_msg" && !strings.Contains(sess.Events[0].Text, "<environment_context>") && strings.Contains(sess.Events[0].Text, "帮我分析一下这个报错"))
		check("encrypted reasoning placeholder", len(sess.Events) > 2 && sess.Events[2].Text == "[思维链已加密，跳过]")
		var paired *core.Event
		for i := range sess.Events {
			if sess.Events[i].Kind == "tool_call" && sess.Events[i].Text == "apply_patch" {
				paired = &sess.Events[i]
			}
		}
		check("tool output paired by call_id", paired != nil && strings.Contains(paired.ToolOutput, "success"))
	}

	// --- render floor ---
	makeTurn := func(user string, n int) []core.Event {
		evs := []core.Event{{Kind: "user_msg", Role: "user", Text: user}}
		for i := 0; i < n; i++ {
			evs = append(evs, core.Event{Kind: "tool_call", Text: "exec", ToolArgs: `{"cmd":"pwd"}`, ToolOutput: strings.Repeat("x", 3000)})
		}
		evs = append(evs, core.Event{Kind: "assistant_msg", Role: "assistant", Text: "完成。" + strings.Repeat("细节", 50)})
		return evs
	}
	var mega []core.Event
	for t := 0; t < 8; t++ {
		mega = append(mega, makeTurn(fmt.Sprintf("第%d轮任务", t), 2)...)
	}
	mega = append(mega, makeTurn("最后一轮：大批量执行", 38)...)
	out := core.RenderSession(core.Session{Meta: core.SessionMeta{ID: sidModern, Cwd: "/x"}, Events: mega}, 10, core.HintsCodex)
	check("newest turn always kept (floor rule)", strings.Contains(out, "最后一轮：大批量执行"))
	check("oldest turns dropped when over budget", !strings.Contains(out, "第0轮任务"))
	check("footer truncation stats present", strings.Contains(out, "截断统计"))
	check("truncated tool output carries marker", strings.Contains(out, "…(截断)"))
	check("file list from apply_patch args", contains(core.FileChanges([]core.Event{{Kind: "tool_call", Text: "apply_patch", ToolArgs: `{"file_path": "src/a.py"}`}}, core.HintsCodex), "src/a.py"))

	// --- Pi ---
	pid := "01234567-89ab-cdef-0123-456789abcdef"
	pdir := filepath.Join(tmp, "pi-sessions", "--Users-demo--")
	os.MkdirAll(pdir, 0o755)
	pfile := filepath.Join(pdir, "2026-08-31T10-00-00-000Z_"+pid+".jsonl")
	var pb strings.Builder
	pjl := func(v any) {
		raw, _ := json.Marshal(v)
		pb.Write(raw)
		pb.WriteByte('\n')
	}
	pjl(map[string]any{"type": "session", "version": 3, "id": pid, "timestamp": "2026-08-31T10:00:00.000Z", "cwd": "/Users/demo"})
	pjl(map[string]any{"type": "session_info", "id": "n1", "parentId": nil, "timestamp": "2026-08-31T10:00:01.000Z", "name": "修报错"})
	pjl(map[string]any{"type": "message", "id": "a1", "parentId": "n1", "timestamp": "2026-08-31T10:00:02.000Z", "message": map[string]any{"role": "user", "content": []any{map[string]any{"type": "text", "text": "主线任务"}}}})
	pjl(map[string]any{"type": "message", "id": "a2", "parentId": "a1", "timestamp": "2026-08-31T10:00:03.000Z", "message": map[string]any{"role": "assistant", "model": "grok-4.6", "content": []any{
		map[string]any{"type": "text", "text": "我来写"},
		map[string]any{"type": "toolCall", "id": "call_w", "name": "write", "arguments": map[string]any{"path": "src/a.py", "content": "x"}},
	}}})
	pjl(map[string]any{"type": "message", "id": "a3", "parentId": "a2", "timestamp": "2026-08-31T10:00:04.000Z", "message": map[string]any{"role": "toolResult", "toolCallId": "call_w", "toolName": "write", "content": []any{map[string]any{"type": "text", "text": "wrote"}}, "isError": false}})
	pjl(map[string]any{"type": "message", "id": "b1", "parentId": "a1", "timestamp": "2026-08-31T10:00:05.000Z", "message": map[string]any{"role": "user", "content": []any{map[string]any{"type": "text", "text": "旁支不要"}}}})
	pjl(map[string]any{"type": "message", "id": "a4", "parentId": "a3", "timestamp": "2026-08-31T10:00:07.000Z", "message": map[string]any{"role": "user", "content": []any{map[string]any{"type": "text", "text": "继续主线"}}}})
	pb.WriteString("not-json\n")
	os.WriteFile(pfile, []byte(pb.String()), 0o644)
	ps := sources.NewPi(filepath.Join(tmp, "pi-sessions"))
	pmetas, _ := ps.ListSessions(20)
	check("pi list finds session", len(pmetas) > 0 && pmetas[0].ID == pid)
	check("pi title from session_info", len(pmetas) > 0 && pmetas[0].Title == "修报错")
	psess, _ := ps.ReadSession(pid)
	if psess != nil {
		texts := []string{}
		for _, e := range psess.Events {
			texts = append(texts, e.Text)
		}
		joined := strings.Join(texts, "\n")
		check("pi leaf path drops branch", !strings.Contains(joined, "旁支不要"))
		check("pi leaf path keeps mainline", strings.Contains(joined, "主线任务") && strings.Contains(joined, "继续主线"))
		ok := false
		for _, e := range psess.Events {
			if e.Kind == "tool_call" && e.Text == "write" && e.ToolOutput == "wrote" {
				ok = true
			}
		}
		check("pi toolCall paired by toolCallId", ok)
		check("pi bad line warned", hasSub(psess.Warnings, "坏行"))
	}

	check("recover-self is claude only", strings.Join(sources.TargetNames("claude", true), ",") == "claude")
	check("recover lists every other source", strings.Join(sources.TargetNames("claude", false), ",") == "codex,pi")

	if len(fail) > 0 {
		fmt.Println("SELF-TEST FAILED: " + strings.Join(fail, ", "))
		return 1
	}
	fmt.Println("SELF-TEST PASSED")
	return 0
}

func setMtime(path, iso string) {
	t, err := time.Parse("2006-01-02T15:04:05", iso)
	if err != nil {
		return
	}
	_ = os.Chtimes(path, t, t)
}

func hasSub(ss []string, sub string) bool {
	for _, s := range ss {
		if strings.Contains(s, sub) {
			return true
		}
	}
	return false
}

func contains(ss []string, v string) bool {
	for _, s := range ss {
		if s == v {
			return true
		}
	}
	return false
}
