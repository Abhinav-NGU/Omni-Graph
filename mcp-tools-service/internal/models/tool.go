package models

type ToolRequest struct {
	Tool  string `json:"tool"`
	Input string `json:"input"`
}

type ToolResponse struct {
	Result interface{} `json:"result,omitempty"`
	Error  string      `json:"error,omitempty"`
}