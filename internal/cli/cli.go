package cli

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/HelloiOS2014/AgentRecovery/internal/core"
	"github.com/HelloiOS2014/AgentRecovery/internal/sources"
)

func Main(args []string) int {
	if len(args) < 1 {
		fmt.Fprintln(os.Stderr, usage())
		return 2
	}
	if args[0] == "self-test" {
		return runSelfTest()
	}
	host := ""
	self, asJSON := false, false
	recent := core.DefaultRecent
	var rest []string
	for i := 0; i < len(args); i++ {
		a := args[i]
		switch {
		case a == "--host" && i+1 < len(args):
			i++
			host = args[i]
		case strings.HasPrefix(a, "--host="):
			host = strings.TrimPrefix(a, "--host=")
		case a == "--self":
			self = true
		case a == "--json":
			asJSON = true
		case a == "--recent" && i+1 < len(args):
			i++
			n, err := strconv.Atoi(args[i])
			if err != nil {
				fmt.Fprintln(os.Stderr, "用法：show <session-id> [--recent N] [--self] [--json]")
				return 2
			}
			recent = n
		default:
			rest = append(rest, a)
		}
	}
	if host == "" {
		fmt.Fprintln(os.Stderr, "缺少 --host claude|codex|pi")
		return 2
	}
	if host != "claude" && host != "codex" && host != "pi" {
		fmt.Fprintln(os.Stderr, "未知 --host：", host)
		return 2
	}
	if len(rest) < 1 {
		fmt.Fprintln(os.Stderr, usage())
		return 2
	}
	switch rest[0] {
	case "list":
		return cmdList(host, 20, self, asJSON)
	case "show":
		if len(rest) < 2 {
			fmt.Fprintln(os.Stderr, usage())
			return 2
		}
		return cmdShow(host, rest[1], recent, self, asJSON)
	default:
		fmt.Fprintln(os.Stderr, usage())
		return 2
	}
}

func usage() string {
	return `AgentRecovery CLI

Usage:
  recover --host claude|codex|pi list [--self] [--json]
  recover --host claude|codex|pi show <session-id> [--recent N] [--self] [--json]
  recover self-test`
}

func archivePath(host, sessionID string) (dir, path string, mode os.FileMode) {
	if host == "claude" {
		home, _ := os.UserHomeDir()
		dir = filepath.Join(home, ".claude", "recover-handoffs")
		return dir, filepath.Join(dir, sessionID+".md"), 0o600
	}
	return ".recover-handoff", filepath.Join(".recover-handoff", sessionID+".md"), 0o600
}

func cmdList(host string, limit int, self, asJSON bool) int {
	srcs := sources.Defaults()
	names := sources.TargetNames(host, self)
	cur, _ := os.Getwd()
	metas, blocked := sources.CollectMetas(srcs, names, limit, cur)
	if asJSON {
		type row struct {
			Source    string `json:"source"`
			ID        string `json:"id"`
			Title     string `json:"title"`
			Cwd       string `json:"cwd"`
			UpdatedAt string `json:"updated_at"`
			StartedAt string `json:"started_at"`
			Model     string `json:"model"`
			Current   bool   `json:"current"`
		}
		payload := map[string]any{"ok": len(metas) > 0, "blocked": blocked}
		var sessions []row
		for _, m := range metas {
			sessions = append(sessions, row{
				Source: m.Source, ID: m.ID, Title: m.Title, Cwd: m.Cwd,
				UpdatedAt: m.UpdatedAt, StartedAt: m.StartedAt, Model: m.Model,
				Current: sources.IsCurrent(m.Cwd, cur),
			})
		}
		if sessions == nil {
			sessions = []row{}
		}
		payload["sessions"] = sessions
		if len(metas) == 0 {
			if len(blocked) > 0 {
				payload["error"] = "permission blocked: " + strings.Join(blocked, ",")
			} else {
				payload["error"] = "no sessions"
			}
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetEscapeHTML(false)
		_ = enc.Encode(payload)
		if len(metas) > 0 {
			return 0
		}
		if len(blocked) > 0 {
			return 2
		}
		return 1
	}
	if len(metas) == 0 {
		if len(blocked) > 0 {
			fmt.Printf("❌ 无权限读取 %s 会话目录（沙箱/权限拦截，不是空列表）\n", strings.Join(blocked, "/"))
			return 2
		}
		fmt.Printf("未检测到 %s 会话（目录不存在或为空）。\n", strings.Join(names, "/"))
		return 1
	}
	if len(blocked) > 0 {
		fmt.Printf("⚠️ 无法读取：%s\n", strings.Join(blocked, ", "))
	}
	fmt.Printf("[%s] 最近 %d 个会话：输入序号或粘贴完整 session ID（* = 当前项目）\n", strings.Join(names, "/"), len(metas))
	for i, m := range metas {
		title := m.Title
		if title == "" {
			title = "无标题"
		}
		if len([]rune(title)) > 24 {
			title = string([]rune(title)[:24])
		}
		mark := " "
		if sources.IsCurrent(m.Cwd, cur) {
			mark = "*"
		}
		src := m.Source
		if src == "" {
			src = "?"
		}
		cwd := m.Cwd
		if cwd == "" {
			cwd = "?"
		}
		upd := m.UpdatedAt
		if len(upd) > 16 {
			upd = upd[:16]
		}
		fmt.Printf("%s%3d. [%s] %-24s %s  cwd=%s  (%s)\n", mark, i+1, src, title, upd, cwd, m.ID)
	}
	fmt.Println("\n用法：/recover <序号> 或 /recover <完整session ID>（跨源自动识别）")
	return 0
}

func fail(msg string, asJSON bool, code int) int {
	if asJSON {
		enc := json.NewEncoder(os.Stdout)
		enc.SetEscapeHTML(false)
		_ = enc.Encode(map[string]any{"ok": false, "error": msg})
	} else {
		fmt.Println(msg)
	}
	return code
}

func cmdShow(host, sessionID string, recent int, self, asJSON bool) int {
	srcs := sources.Defaults()
	var name string
	var src sources.Source
	if n, err := strconv.Atoi(sessionID); err == nil && n >= 1 {
		names := sources.TargetNames(host, self)
		cur, _ := os.Getwd()
		metas, _ := sources.CollectMetas(srcs, names, 20, cur)
		if n > len(metas) {
			return fail(fmt.Sprintf("序号 %s 超出范围（有效 1..%d）", sessionID, len(metas)), asJSON, 1)
		}
		picked := metas[n-1]
		sessionID = picked.ID
		name, src = picked.Source, srcs[picked.Source]
	} else {
		for _, cand := range sources.Order {
			inst := srcs[cand]
			s, err := inst.ReadSession(sessionID)
			if err != nil {
				if errors.Is(err, sources.ErrNotFound) || strings.Contains(err.Error(), "未找到") {
					continue
				}
				if os.IsPermission(err) {
					return fail("❌ 无权限读取 "+cand+" 源（沙箱/权限拦截）", asJSON, 2)
				}
				continue
			}
			_ = s
			name, src = cand, inst
			break
		}
		if src == nil {
			return fail("未找到会话 "+sessionID+"（已同时搜索所有会话存储）", asJSON, 1)
		}
	}
	session, err := src.ReadSession(sessionID)
	if err != nil {
		if os.IsPermission(err) {
			return fail("❌ 无权限读取 "+name+" 源（沙箱/权限拦截）", asJSON, 2)
		}
		return fail(err.Error(), asJSON, 1)
	}
	session.Meta.Source = name
	var extra []string
	cur, _ := os.Getwd()
	if session.Meta.Cwd != "" && !sources.IsCurrent(session.Meta.Cwd, cur) {
		w := fmt.Sprintf("此会话来自其他项目：%s（当前目录：%s），路径请注意核对", session.Meta.Cwd, cur)
		extra = append(extra, w)
		if !asJSON {
			fmt.Println("⚠️ " + w)
		}
	}
	if session.Meta.Title == "" {
		if t := src.LoadTitles()[sessionID]; t != "" {
			session.Meta.Title = t
		}
	}
	text := core.RenderSession(*session, recent, core.HintsFor(name))
	dir, apath, _ := archivePath(host, sessionID)
	var archive string
	if err := os.MkdirAll(dir, 0o700); err != nil {
		extra = append(extra, "存档失败："+err.Error())
	} else if err := os.WriteFile(apath, []byte(text), 0o600); err != nil {
		extra = append(extra, "存档失败："+err.Error())
	} else {
		archive = apath
	}
	if asJSON {
		enc := json.NewEncoder(os.Stdout)
		enc.SetEscapeHTML(false)
		_ = enc.Encode(map[string]any{
			"ok":     true,
			"source": name,
			"id":     sessionID,
			"meta": map[string]any{
				"title":      session.Meta.Title,
				"cwd":        session.Meta.Cwd,
				"model":      session.Meta.Model,
				"started_at": session.Meta.StartedAt,
				"updated_at": session.Meta.UpdatedAt,
			},
			"handoff":   text,
			"warnings":  append(extra, session.Warnings...),
			"archive":   archive,
			"compacted": session.Compacted,
		})
		return 0
	}
	fmt.Println(text)
	if archive != "" {
		if host == "claude" {
			fmt.Printf("\n[存档] %s\n", archive)
		} else {
			fmt.Printf("\n[存档] %s（工作区内，沙箱可写）\n", archive)
		}
	} else {
		for _, w := range extra {
			if strings.HasPrefix(w, "存档失败") {
				fmt.Printf("[警告] %s\n", w)
			}
		}
	}
	return 0
}
