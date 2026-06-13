package handlers

import (
	"github.com/gofiber/fiber/v2"
	"github.com/omnigraph/mcp-tools-service/internal/models"
	"github.com/omnigraph/mcp-tools-service/internal/tools"
)

// ExecuteToolEndpoint dispatches tool execution requests.
// POST /tools/execute
// Body: {"tool": "calculator", "input": "2 + 2"}
func ExecuteToolEndpoint(c *fiber.Ctx) error {
	var req models.ToolRequest

	if err := c.BodyParser(&req); err != nil {
		return c.Status(fiber.StatusBadRequest).JSON(models.ToolResponse{
			Error: "failed to parse request JSON",
		})
	}

	if req.Tool == "" {
		return c.Status(fiber.StatusBadRequest).JSON(models.ToolResponse{
			Error: "field 'tool' is required",
		})
	}

	executor, exists := tools.Registry[req.Tool]
	if !exists {
		// Return available tools in the error to help the agent
		available := make([]string, 0, len(tools.Registry))
		for name := range tools.Registry {
			available = append(available, name)
		}
		return c.Status(fiber.StatusNotFound).JSON(fiber.Map{
			"error":           "tool not found: " + req.Tool,
			"available_tools": available,
		})
	}

	result, err := executor(req.Input)
	if err != nil {
		return c.Status(fiber.StatusInternalServerError).JSON(models.ToolResponse{
			Error: err.Error(),
		})
	}

	return c.JSON(models.ToolResponse{Result: result})
}