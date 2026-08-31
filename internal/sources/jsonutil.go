package sources

import (
	"bufio"
	"encoding/json"
	"os"
)

func asMap(v any) map[string]any {
	m, _ := v.(map[string]any)
	return m
}

func asStr(v any) string {
	s, _ := v.(string)
	return s
}

func asList(v any) []any {
	s, _ := v.([]any)
	return s
}

func asBool(v any) bool {
	b, _ := v.(bool)
	return b
}

func readJSONL(path string) (records []map[string]any, bad int, err error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 16*1024*1024)
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var d map[string]any
		if json.Unmarshal(line, &d) != nil {
			bad++
			continue
		}
		records = append(records, d)
	}
	return records, bad, sc.Err()
}

func jsonDump(v any) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	b, err := json.Marshal(v)
	if err != nil {
		return ""
	}
	return string(b)
}
