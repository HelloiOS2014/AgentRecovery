package core

import (
	"regexp"
	"strings"
	"unicode/utf8"
)

const (
	RecentBudget  = 40000
	HistBudget    = 20000
	HistTurns     = 50
	FileListCap   = 40
	DefaultRecent = 10
	Trunc         = "…(截断)"
)

var Caps = map[string]int{
	"usr": 1000, "asst": 1500, "args": 600, "out": 1200, "reason": 100,
}

var (
	HintsCodex  = []string{"write", "apply_patch", "edit"}
	HintsClaude = []string{"write", "edit", "multiedit", "notebookedit", "apply_patch"}
	HintsPi     = []string{"write", "edit"}
)

type SessionMeta struct {
	ID        string
	Title     string
	Cwd       string
	StartedAt string
	UpdatedAt string
	Model     string
	Source    string
}

type Event struct {
	Kind       string // user_msg | assistant_msg | reasoning | tool_call | tool_output
	Role       string
	Text       string
	ToolArgs   string
	ToolOutput string
}

type Session struct {
	Meta      SessionMeta
	Events    []Event
	Compacted bool
	Warnings  []string
}

func HintsFor(src string) []string {
	switch src {
	case "claude":
		return HintsClaude
	case "pi":
		return HintsPi
	default:
		return HintsCodex
	}
}

func Truncate(text string, cap int) string {
	out, _ := cut(text, cap)
	return out
}

func cut(text string, cap int) (string, bool) {
	if utf8.RuneCountInString(text) <= cap {
		return text, false
	}
	r := []rune(text)
	return string(r[:cap]) + Trunc, true
}

var (
	reFilePath = regexp.MustCompile(`"file_path"\s*:\s*"([^"]+)"`)
	rePath     = regexp.MustCompile(`"path"\s*:\s*"([^"]+)"`)
	rePatch    = regexp.MustCompile(`---\s+a/(\S+)`)
)

func FileChanges(events []Event, hints []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, e := range events {
		if e.Kind != "tool_call" {
			continue
		}
		name := strings.ToLower(e.Text)
		ok := false
		for _, h := range hints {
			if strings.Contains(name, h) {
				ok = true
				break
			}
		}
		if !ok {
			continue
		}
		args := e.ToolArgs
		var path string
		if m := reFilePath.FindStringSubmatch(args); len(m) > 1 {
			path = m[1]
		} else if m := rePath.FindStringSubmatch(args); len(m) > 1 {
			path = m[1]
		} else if m := rePatch.FindStringSubmatch(args); len(m) > 1 {
			path = m[1]
		}
		if path == "" || seen[path] {
			continue
		}
		seen[path] = true
		out = append(out, path)
	}
	return out
}

func clip16(s string) string {
	if s == "" {
		return "?"
	}
	r := []rune(s)
	if len(r) > 16 {
		return string(r[:16])
	}
	return s
}

func RenderSession(session Session, recent int, hints []string) string {
	if hints == nil {
		hints = HintsCodex
	}
	meta := session.Meta
	var lines []string
	truncated := 0
	cutCount := func(text string, cap int) string {
		out, hit := cut(text, cap)
		if hit {
			truncated++
		}
		return out
	}

	lines = append(lines, "# 恢复的会话上下文（"+meta.ID+"）")
	if meta.Source != "" {
		lines = append(lines, "- 来源："+meta.Source)
	}
	title := meta.Title
	if title == "" {
		title = "无标题"
	}
	lines = append(lines, "- 标题："+title)
	lines = append(lines, "- 时间："+clip16(meta.StartedAt)+" → "+clip16(meta.UpdatedAt))
	cwd := meta.Cwd
	if cwd == "" {
		cwd = "?"
	}
	lines = append(lines, "- 原工作目录：`"+cwd+"`")
	if meta.Model != "" {
		lines = append(lines, "- 模型："+meta.Model)
	}
	if session.Compacted {
		lines = append(lines, "- ⚠️ 该会话已压缩：工具调用细节不可用，仅消息骨架")
	}
	lines = append(lines, "- 此文件包含工具输出，可能含密钥；请勿外发", "")

	var turns [][]Event
	for _, e := range session.Events {
		if e.Kind == "user_msg" {
			turns = append(turns, []Event{e})
		} else if len(turns) > 0 {
			turns[len(turns)-1] = append(turns[len(turns)-1], e)
		}
	}

	type statsT struct{ turns, recentKept, recentDropped, histKept, histDropped, truncated, files int }
	st := statsT{turns: len(turns)}

	var hist [][]Event
	if recent < len(turns) {
		hist = turns[:len(turns)-recent]
	}
	var keptHist []string
	histSize := 0
	for i := len(hist) - 1; i >= 0; i-- {
		t := hist[i]
		user := strings.TrimSpace(t[0].Text)
		if user == "" {
			user = "[空]"
		}
		user = cutCount(user, 200)
		asst := ""
		var tools []string
		for _, e := range t {
			if e.Kind == "assistant_msg" && asst == "" {
				asst = cutCount(strings.TrimSpace(e.Text), 400)
			}
			if e.Kind == "tool_call" {
				tools = append(tools, e.Text)
			}
		}
		if asst == "" {
			asst = "(无回复)"
		}
		block := "- 用户：" + user + "\n- 助手：" + asst + "\n- 工具：" + strings.Join(tools, "，")
		if len(keptHist) >= HistTurns || histSize+len(block) > HistBudget {
			st.histDropped++
			continue
		}
		keptHist = append(keptHist, block)
		histSize += len(block)
	}
	st.histKept = len(keptHist)

	var recents [][]Event
	if recent > 0 && len(turns) > 0 {
		start := len(turns) - recent
		if start < 0 {
			start = 0
		}
		recents = turns[start:]
	}
	var keptRec []string
	recSize := 0
	for i := len(recents) - 1; i >= 0; i-- {
		t := recents[i]
		var blockLines []string
		for _, e := range t {
			switch e.Kind {
			case "user_msg":
				text := strings.TrimSpace(e.Text)
				if text == "" {
					text = "[空]"
				}
				blockLines = append(blockLines, "**用户**："+cutCount(text, Caps["usr"]))
			case "assistant_msg":
				blockLines = append(blockLines, "**助手**："+cutCount(strings.TrimSpace(e.Text), Caps["asst"]))
			case "reasoning":
				blockLines = append(blockLines, "> "+cutCount(strings.TrimSpace(e.Text), Caps["reason"]))
			case "tool_call":
				name := e.Text
				if name == "" {
					name = "?"
				}
				blockLines = append(blockLines, "`工具` "+name+"："+cutCount(strings.TrimSpace(e.ToolArgs), Caps["args"]))
				if e.ToolOutput != "" {
					blockLines = append(blockLines, "`输出` "+cutCount(e.ToolOutput, Caps["out"]))
				}
			}
		}
		block := strings.Join(blockLines, "\n")
		isNewest := i == len(recents)-1
		if !isNewest && recSize+len(block) > RecentBudget {
			st.recentDropped++
			continue
		}
		keptRec = append(keptRec, block)
		recSize += len(block)
	}
	for i, j := 0, len(keptRec)-1; i < j; i, j = i+1, j-1 {
		keptRec[i], keptRec[j] = keptRec[j], keptRec[i]
	}
	st.recentKept = len(keptRec)
	st.truncated = truncated

	if len(keptRec) > 0 {
		lines = append(lines, "## 最近现场（完整保真，逐项上限内）")
		lines = append(lines, keptRec...)
	}
	if len(keptHist) > 0 {
		lines = append(lines, "\n## 更早历史（压缩）")
		lines = append(lines, keptHist...)
	}

	files := FileChanges(session.Events, hints)
	st.files = len(files)
	lines = append(lines, "\n## 文件改动")
	if len(files) == 0 {
		lines = append(lines, "（无识别出的写文件操作）")
	} else {
		n := len(files)
		if n > FileListCap {
			n = FileListCap
		}
		for _, f := range files[:n] {
			lines = append(lines, "- "+f)
		}
		if len(files) > FileListCap {
			lines = append(lines, "- +"+itoa(len(files)-FileListCap)+" 更多")
		}
	}

	lines = append(lines, "\n## 截断统计")
	lines = append(lines, "- 总轮数 "+itoa(st.turns)+"；最近区保留 "+itoa(st.recentKept)+" 轮、丢弃 "+itoa(st.recentDropped)+
		" 轮；历史区保留 "+itoa(st.histKept)+" 轮、丢弃 "+itoa(st.histDropped)+" 轮")
	lines = append(lines, "- 逐项截断 "+itoa(st.truncated)+" 条；文件清单 "+itoa(st.files)+" 条（上限 "+itoa(FileListCap)+"）")
	for _, w := range session.Warnings {
		lines = append(lines, "- ⚠️ "+w)
	}
	lines = append(lines,
		"\n## 继续任务",
		"此前的任务目标是恢复此会话的未完成工作。核对当前工作目录是否与原目录一致，",
		"注意工作区可能有未提交改动；然后继续任务。",
	)
	return strings.Join(lines, "\n")
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}
