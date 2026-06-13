package tools

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// ddgResult represents a single DuckDuckGo Instant Answer API result.
type ddgResult struct {
	AbstractText   string `json:"AbstractText"`
	AbstractSource string `json:"AbstractSource"`
	AbstractURL    string `json:"AbstractURL"`
	Answer         string `json:"Answer"`
	AnswerType     string `json:"AnswerType"`
	Definition     string `json:"Definition"`
	DefinitionURL  string `json:"DefinitionURL"`
	Heading        string `json:"Heading"`
	RelatedTopics  []struct {
		Text     string `json:"Text"`
		FirstURL string `json:"FirstURL"`
	} `json:"RelatedTopics"`
}

var httpClient = &http.Client{Timeout: 10 * time.Second}

// WebSearch queries the DuckDuckGo Instant Answer API (no API key needed).
// For production, swap with Serper, Brave Search, or Tavily for full results.
func WebSearch(query string) (interface{}, error) {
	query = strings.TrimSpace(query)
	if query == "" {
		return nil, fmt.Errorf("search query must not be empty")
	}

	endpoint := "https://api.duckduckgo.com/?q=" +
		url.QueryEscape(query) +
		"&format=json&no_redirect=1&no_html=1&skip_disambig=1"

	resp, err := httpClient.Get(endpoint)
	if err != nil {
		return nil, fmt.Errorf("web search request failed: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read search response: %w", err)
	}

	var result ddgResult
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("failed to parse search response: %w", err)
	}

	// Build a clean, structured response
	output := map[string]interface{}{
		"query": query,
	}

	// Prefer direct answer, then abstract, then related topics
	if result.Answer != "" {
		output["answer"] = result.Answer
		output["type"] = result.AnswerType
	} else if result.AbstractText != "" {
		output["answer"] = result.AbstractText
		output["source"] = result.AbstractSource
		output["url"] = result.AbstractURL
		output["heading"] = result.Heading
	} else if result.Definition != "" {
		output["answer"] = result.Definition
		output["url"] = result.DefinitionURL
	} else {
		// Fall back to related topics
		topics := []map[string]string{}
		for i, t := range result.RelatedTopics {
			if i >= 5 {
				break
			}
			if t.Text != "" {
				topics = append(topics, map[string]string{
					"text": t.Text,
					"url":  t.FirstURL,
				})
			}
		}
		if len(topics) > 0 {
			output["related_topics"] = topics
		} else {
			output["answer"] = "No results found for: " + query
		}
	}

	return output, nil
}