package routes

import (
	"github.com/gofiber/fiber/v2"
	"github.com/omnigraph/mcp-tools-service/internal/handlers"
	"github.com/omnigraph/mcp-tools-service/internal/tools"
)

func Setup(app *fiber.App) {
	// Health check
	app.Get("/health", func(c *fiber.Ctx) error {
		return c.JSON(fiber.Map{"status": "ok", "service": "mcp-tools-service"})
	})

	// List available tools
	app.Get("/tools", func(c *fiber.Ctx) error {
		names := make([]string, 0, len(tools.Registry))
		for name := range tools.Registry {
			names = append(names, name)
		}
		return c.JSON(fiber.Map{"tools": names})
	})

	// Execute a tool
	app.Post("/tools/execute", handlers.ExecuteToolEndpoint)
}