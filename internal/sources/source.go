package sources

import (
	"errors"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/HelloiOS2014/AgentRecovery/internal/core"
)

var ErrNotFound = errors.New("session not found")

type Source interface {
	Name() string
	ListSessions(limit int, preferCwd string) ([]core.SessionMeta, error)
	ReadSession(id string) (*core.Session, error)
	LoadTitles() map[string]string
}

type fileHit struct {
	ID    string
	Path  string
	Mtime int64
}

// selectHits keeps current-project sessions first (up to limit), then fills
// with the newest others. preferCwd sessions are included even when older
// than the global top-N.
func selectHits(hits []fileHit, limit int, preferCwd string, load func(path, id string) core.SessionMeta) []core.SessionMeta {
	sort.Slice(hits, func(i, j int) bool { return hits[i].Mtime > hits[j].Mtime })
	if limit <= 0 {
		limit = len(hits)
	}
	seen := map[string]bool{}
	var out []core.SessionMeta
	add := func(h fileHit) core.SessionMeta {
		m := load(h.Path, h.ID)
		if m.ID == "" {
			m.ID = h.ID
		}
		m.UpdatedAt = isoSecUnix(h.Mtime)
		return m
	}
	if preferCwd != "" {
		for _, h := range hits {
			if len(out) >= limit {
				break
			}
			m := add(h)
			if IsCurrent(m.Cwd, preferCwd) {
				out = append(out, m)
				seen[h.ID] = true
			}
		}
	}
	for _, h := range hits {
		if len(out) >= limit {
			break
		}
		if seen[h.ID] {
			continue
		}
		out = append(out, add(h))
		seen[h.ID] = true
	}
	return out
}

func isoSecUnix(nsec int64) string {
	return isoSec(time.Unix(0, nsec))
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

func CollectMetas(instances map[string]Source, names []string, limit int, preferCwd string) (metas []core.SessionMeta, blocked []string) {
	var tagged []core.SessionMeta
	for _, name := range names {
		src := instances[name]
		if src == nil {
			continue
		}
		list, err := src.ListSessions(limit, preferCwd)
		if err != nil {
			blocked = append(blocked, name)
			continue
		}
		for i := range list {
			list[i].Source = name
			tagged = append(tagged, list[i])
		}
	}
	var current, others []core.SessionMeta
	for _, m := range tagged {
		if preferCwd != "" && IsCurrent(m.Cwd, preferCwd) {
			current = append(current, m)
		} else {
			others = append(others, m)
		}
	}
	sort.SliceStable(current, func(i, j int) bool { return current[i].UpdatedAt > current[j].UpdatedAt })
	sort.SliceStable(others, func(i, j int) bool { return others[i].UpdatedAt > others[j].UpdatedAt })
	metas = append(metas, current...)
	if limit > 0 && len(metas) >= limit {
		return metas[:limit], blocked
	}
	need := limit - len(metas)
	if limit <= 0 {
		metas = append(metas, others...)
		return metas, blocked
	}
	if need > len(others) {
		need = len(others)
	}
	metas = append(metas, others[:need]...)
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
