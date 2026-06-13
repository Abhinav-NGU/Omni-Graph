package main

import (
	"log"

	"github.com/gofiber/fiber/v2"
	"github.com/omnigraph/mcp-tools-service/routes"
)

func main() {
	app := fiber.New(fiber.Config{
		AppName: "OmniGraph MCP Tools Service",
	})

	routes.Setup(app)

	log.Fatal(app.Listen(":8080"))
}