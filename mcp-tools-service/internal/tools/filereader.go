package tools

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const (
	// maxFileSize caps reads at 1MB to prevent memory issues
	maxFileSize = 1 * 1024 * 1024

	// allowedBasePath restricts file access to this directory
	// Override via FILE_READER_BASE_PATH environment variable
	defaultBasePath = "/data"
)

// ReadFile reads a file from the allowed base path.
// Path traversal attacks (../../etc/passwd) are blocked.
func ReadFile(path string) (interface{}, error) {
	path = strings.TrimSpace(path)
	if path == "" {
		return nil, fmt.Errorf("file path must not be empty")
	}

	// Determine allowed base path
	basePath := os.Getenv("FILE_READER_BASE_PATH")
	if basePath == "" {
		basePath = defaultBasePath
	}

	// Resolve absolute path and verify it stays within basePath
	absPath := filepath.Join(basePath, path)
	cleanBase, err := filepath.Abs(basePath)
	if err != nil {
		return nil, fmt.Errorf("invalid base path: %w", err)
	}
	cleanAbs, err := filepath.Abs(absPath)
	if err != nil {
		return nil, fmt.Errorf("invalid file path: %w", err)
	}

	// Block path traversal
	if !strings.HasPrefix(cleanAbs, cleanBase+string(os.PathSeparator)) &&
		cleanAbs != cleanBase {
		return nil, fmt.Errorf("access denied: path outside allowed directory")
	}

	// Check file exists and is a regular file
	info, err := os.Stat(cleanAbs)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("file not found: %s", path)
		}
		return nil, fmt.Errorf("cannot access file: %w", err)
	}
	if info.IsDir() {
		return nil, fmt.Errorf("path is a directory, not a file: %s", path)
	}
	if info.Size() > maxFileSize {
		return nil, fmt.Errorf(
			"file too large: %d bytes (max %d bytes)", info.Size(), maxFileSize,
		)
	}

	data, err := os.ReadFile(cleanAbs)
	if err != nil {
		return nil, fmt.Errorf("failed to read file: %w", err)
	}

	return map[string]interface{}{
		"path":    path,
		"size":    info.Size(),
		"content": string(data),
	}, nil
}