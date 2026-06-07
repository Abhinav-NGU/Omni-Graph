# OmniGraph Project

Welcome to the OmniGraph project! This platform is designed as a sophisticated, microservices-based system for building and managing agentic workflows powered by a knowledge graph.

## Current Status

The project has a fully containerized local development environment orchestrated by Docker Compose. The core `orchestration-service` is implemented with a robust initialization process, including configuration management, database connection lifecycle, and deep health checks.

## Architecture

The OmniGraph platform is built on a set of interconnected services, each running in its own Docker container.

| Service                 | Technology          | Purpose                                                              |
| ----------------------- | ------------------- | -------------------------------------------------------------------- |
| **Traefik**             | Go                  | An API Gateway that handles incoming requests and routes them.       |
| **Qdrant**              | Rust                | A vector database for storing and searching vector embeddings.       |
| **Neo4j** (Disabled)    | Java                | A graph database for storing and querying knowledge graph data.      |
| **Ollama**              | Go                  | A local LLM engine for serving language models like Llama 3.         |
| **Jaeger**              | Go                  | A distributed tracing system for observability.                      |
| **orchestration-service** | Python (FastAPI)    | The core custom service for orchestrating workflows.                 |
| **mcp-tools-service** (Planned) | Go (Fiber)          | A future service for providing specialized tools.                    |

### Orchestration Service

This is the central nervous system of the platform. It is a Python application built with FastAPI and includes:

*   **Configuration Management**: Securely loads settings from the `.env` file using `pydantic-settings`.
*   **Database Lifecycle**: Manages connections to Qdrant (and Neo4j when enabled) using a `DatabaseManager` class. It features a robust connection retry mechanism (`tenacity`) to handle startup race conditions.
*   **Lifespan Management**: Uses FastAPI's `lifespan` events to gracefully connect to and disconnect from databases on application startup and shutdown.
*   **Deep Health Checks**: Provides a `/health` endpoint that actively verifies connectivity to its dependent database services.

## Getting Started

Follow these steps to get the OmniGraph stack running on your local machine.

### 1. Prerequisites

*   **Docker Desktop** (or Docker Engine with Docker Compose).
*   **(Optional for GPU)** The NVIDIA Container Toolkit if you plan to use GPU acceleration for Ollama.

### 2. Configuration

The project uses an `.env` file for configuration. Before starting, you should review it and set a secure password for Neo4j (even though it is currently disabled).

```dotenv
# .env

# Docker Compose Project Name
COMPOSE_PROJECT_NAME=omnigraph

# Neo4j Credentials
NEO4J_USER=neo4j
NEO4J_PASSWORD=yourSuperSecurePassword!

# Service Endpoints (for internal communication)
NEO4J_URI=bolt://neo4j:7687
QDRANT_URL=http://qdrant:6333
OLLAMA_BASE_URL=http://ollama:11434

# Jaeger Agent (for OpenTelemetry)
JAEGER_AGENT_HOST=jaeger
JAEGER_AGENT_PORT=6831
```

### 3. Build and Run the Stack

Navigate to the project's root directory (where `docker-compose.yml` is located) and run the following command:

```bash
docker-compose up --build -d
```

*   `--build`: This flag builds the Docker image for the `orchestration-service` from its `Dockerfile`.
*   `-d`: This runs the containers in detached mode, so they run in the background.

### 4. Prepare Ollama Models (First-Time Setup)

The first time you run the stack, the `ollama` container starts without any language models. You must pull the required models into it.

Open a new terminal **after** the `docker-compose up` command has finished and run the following:

```bash
# Pull the embedding model used for vector search
docker exec -it ollama ollama pull nomic-embed-text

# Pull the instruction-tuned model for graph extraction
docker exec -it ollama ollama pull llama3
```

The application will log warnings on startup if these models are missing, but ingestion will fail until they are installed.

### 5. Verify the Services

You can check the status of your running containers with:

```bash
docker-compose ps
```

You should see all active services (`traefik`, `qdrant`, `ollama`, `jaeger`, `orchestration-service`) in a `running` or `healthy` state.

## Service Endpoints

Once the stack is running, you can access the various components through your browser:

| Service                   | URL                               | Description                               |
| ------------------------- | --------------------------------- | ----------------------------------------- |
| **Orchestration Service** | http://localhost:8000             | The main API for the service.             |
| **Health Check**          | http://localhost:8000/health      | Deep health check for the service.        |
| **Traefik Dashboard**     | http://localhost:8080             | UI for the API Gateway.                   |
| **Qdrant Dashboard**      | http://localhost:6333/dashboard   | Web UI for the Qdrant vector database.    |
| **Jaeger UI**             | http://localhost:16686            | UI for viewing distributed traces.        |
| **Ollama API**            | http://localhost:11434            | API endpoint for the local LLM.           |

---

*This README was generated by Gemini Code Assist based on the project's current state.*