package tools

import "time"

// Registry is the single source of truth for all available tools.
var Registry = map[string]func(string) (interface{}, error){
	"calculator":   ExecuteCalculator,
	"file_reader":  ReadFile,
	"web_search":   WebSearch,
	"current_time": CurrentTime,
}

// CurrentTime returns the current UTC time.
func CurrentTime(_ string) (interface{}, error) {
	now := time.Now().UTC()
	return map[string]interface{}{
		"utc":      now.Format(time.RFC3339),
		"unix":     now.Unix(),
		"date":     now.Format("2006-01-02"),
		"time":     now.Format("15:04:05"),
		"timezone": "UTC",
	}, nil
}