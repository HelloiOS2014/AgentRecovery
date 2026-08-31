package sources

import (
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/HelloiOS2014/AgentRecovery/internal/core"
)

var ErrNotFound = errors.New("session not found")

type Source interface {
	Name() string
	ListSessions(limit int) ([]core.SessionMeta, error)
	ReadSession(id string) (*core.Session, error)
	LoadTitles() map[string]string
}

var Order = []string{"codex", "claude", "pi"}

func Defaults() map[string]Source {
	return map[string]Source{
		"codex":  NewCodex(nil, ""),
		"claude": NewClaude(""),
		"pi":     NewPi(""),
	}
}

func TargetNames(self string, selfMode bool) []string {
	if selfMode {
		return []string{self}
	}
	var out []string
	for _, n := range Order {
		if n != self {
			out = append(out, n)
		}
	}
	return out
}

func CollectMetas(instances map[string]Source, names []string, limit int) (metas []core.SessionMeta, blocked []string) {
	for _, name := range names {
		src := instances[name]
		if src == nil {
			continue
		}
		list, err := src.ListSessions(limit)
		if err != nil {
			if os.IsPermission(err) || isBlocked(err) {
				blocked = append(blocked, name)
				continue
			}
			blocked = append(blocked, name)
			continue
		}
		for i := range list {
			list[i].Source = name
			metas = append(metas, list[i])
		}
	}
	sort.SliceStable(metas, func(i, j int) bool { return metas[i].UpdatedAt > metas[j].UpdatedAt })
	if limit > 0 && len(metas) > limit {
		metas = metas[:limit]
	}
	return metas, blocked
}

func isBlocked(err error) bool {
	if err == nil {
		return false
	}
	s := err.Error()
	return strings.Contains(s, "permission") || strings.Contains(s, "operation not permitted")
}

func IsCurrent(cwd, cur string) bool {
	if cwd == "" {
		return false
	}
	a, err1 := filepath.EvalSymlinks(cwd)
	b, err2 := filepath.EvalSymlinks(cur)
	if err1 != nil {
		a = cwd
	}
	if err2 != nil {
		b = cur
	}
	return a == b
}

func SortByCurrent(metas []core.SessionMeta, cur string) []core.SessionMeta {
	sort.SliceStable(metas, func(i, j int) bool {
		ci, cj := IsCurrent(metas[i].Cwd, cur), IsCurrent(metas[j].Cwd, cur)
		if ci == cj {
			return false
		}
		return ci && !cj
	})
	return metas
}
