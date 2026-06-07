# OmniGraph Project

Welcome to the OmniGraph project! A sophisticated, microservices-based system for building and managing agentic workflows powered by a knowledge graph and local LLMs.

## Project Status: Phase 3 Complete ✅

---

## Architecture

| Service | Technology | Purpose |
|---|---|---|
| **Traefik** | Go | API Gateway — routes all incoming requests |
| **Qdrant** | Rust | Vector database for semantic chunk search |
| **Neo4j** | Java | Graph database for entity relationship storage |
| **Ollama** | Go | Local LLM engine (llama3.2 + nomic-embed-text) |
| **Jaeger** | Go | Distributed tracing and observability |
| **orchestration-service** | Python (FastAPI) | Core service — ingestion, RAG pipeline, LangGraph agent |
| **mcp-tools-service** | Go (Fiber) | *(Planned)* Specialized external tools for the agent |

---

## What's Been Built

### Phase 1 — Infrastructure
- Fully containerized stack via Docker Compose
- Traefik API gateway, Jaeger tracing
- Neo4j, Qdrant, Ollama services
- Configuration management via `.env` + `pydantic-settings`
- Database connection lifecycle with retry logic (`tenacity`)
- Deep health checks at `/health`

### Phase 2 — RAG Pipeline
- `/ingest` — accepts raw text, chunks it, generates embeddings (`nomic-embed-text`), extracts a knowledge graph (`llama3.2`)
- Dual storage: Qdrant (vector search) + Neo4j (graph context)
- Content-hash-based deduplication — re-ingesting the same text is safe
- `/query` — semantic vector search + graph path retrieval + LLM synthesis
- `/collections/clear` — admin endpoint to wipe Qdrant collection

### Phase 3 — LangGraph Agent
- `/chat` — agentic multi-turn conversation endpoint
- Smart routing: agent decides between `vector`, `graph`, or `both` strategies based on question type
- Retry logic: if context is insufficient, agent escalates strategy and retries (up to 2x)
- In-memory session store — conversation history persists across turns within a session
- `/chat/{session_id}/history` — view conversation history
- `DELETE /chat/{session_id}` — clear a session

---

## API Endpoints

| Method | Endpoint | Tag | Description |
|---|---|---|---|
| GET | `/` | General | Service info |
| GET | `/health` | Health | Deep health check (Neo4j + Qdrant) |
| POST | `/ingest` | Ingestion | Queue text for background ingestion |
| POST | `/query` | Query | Simple RAG query (no agent, no history) |
| POST | `/chat` | Agent | Agentic chat with routing + memory |
| GET | `/chat/{id}/history` | Agent | Get session conversation history |
| DELETE | `/chat/{id}` | Agent | Clear a session |
| POST | `/collections/clear` | Admin | Wipe Qdrant collection |

Full interactive docs at `http://localhost:8000/docs`

---

## Getting Started

### 1. Prerequisites
- Docker Desktop (or Docker Engine + Docker Compose)
- *(Optional)* NVIDIA Container Toolkit for GPU acceleration

### 2. Configuration

Copy `.env.example` to `.env` and configure:

```dotenv
COMPOSE_PROJECT_NAME=omnigraph

NEO4J_USER=neo4j
NEO4J_PASSWORD=yourSuperSecurePassword!

NEO4J_URI=bolt://neo4j:7687
QDRANT_URL=http://qdrant:6333
OLLAMA_BASE_URL=http://ollama:11434

JAEGER_AGENT_HOST=jaeger
JAEGER_AGENT_PORT=6831

# API Key (for authentication)
API_KEY=your-secret-api-key-here
```

### 3. Build and Run

```bash
docker-compose up --build -d
```

### 4. Pull Ollama Models (First Time Only)

```bash
docker exec -it ollama ollama pull nomic-embed-text
docker exec -it ollama ollama pull llama3.2
```

### 5. Verify

```bash
docker-compose ps
```

All services should be `running` or `healthy`.

---

## Security — API Key Authentication

**All data-modifying and data-accessing endpoints require API key authentication** for security purposes.

### Protected Endpoints
The following endpoints require the `X-API-Key` header:
- `/ingest` — text ingestion
- `/ingest/pdf` — PDF ingestion
- `/ingest/url` — URL ingestion
- `/query` — RAG queries
- `/chat` — agentic chat
- `/chat/{session_id}` — session management
- `/chat/{session_id}/history` — conversation history
- `/collections/clear` — Qdrant admin
- `/graph/clear` — Neo4j admin

### Public Endpoints
These endpoints do NOT require authentication:
- `GET /` — service info
- `GET /health` — health check
- `GET /docs` — Swagger docs

### How to Provide the API Key
Include the API key in every protected request via the **`X-API-Key`** header:

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-secret-api-key-here" \
  -d '{"text": "Your text here"}'
```

### Error Responses
- **401 Unauthorized** — missing `X-API-Key` header
- **403 Forbidden** — invalid or incorrect API key

---

## Service URLs

| Service | URL | Description |
|---|---|---|
| Orchestration API | http://localhost:8000 | Main API |
| Swagger Docs | http://localhost:8000/docs | Interactive API docs |
| Traefik Dashboard | http://localhost:8080 | API gateway UI |
| Qdrant Dashboard | http://localhost:6333/dashboard | Vector DB UI |
| Neo4j Browser | http://localhost:7474 | Graph DB UI |
| Jaeger UI | http://localhost:16686 | Distributed tracing |
| Ollama API | http://localhost:11434 | Local LLM API |

---

## Usage Examples

> **Note:** All examples below include the required `X-API-Key` header. Replace `your-secret-api-key-here` with the value from your `.env` file.

### Ingest a document
```bash
curl -X POST http://localhost:8000/ingest \
-H "Content-Type: application/json" \
-H "X-API-Key: your-secret-api-key-here" \
-d '{"text": "Elon Musk founded SpaceX in 2002 and Tesla in 2003."}'
```

### Simple query (Phase 2)
```bash
curl -X POST http://localhost:8000/query \
-H "Content-Type: application/json" \
-H "X-API-Key: your-secret-api-key-here" \
-d '{"question": "Who founded SpaceX?"}'
```

### Agentic chat (Phase 3)
```bash
# First message — no session_id needed
curl -X POST http://localhost:8000/chat \
-H "Content-Type: application/json" \
-H "X-API-Key: your-secret-api-key-here" \
-d '{"question": "Who founded SpaceX?"}'

# Follow-up — use the session_id from the response above
curl -X POST http://localhost:8000/chat \
-H "Content-Type: application/json" \
-H "X-API-Key: your-secret-api-key-here" \
-d '{"question": "What else did he found?", "session_id": "your-session-id"}'
```

### Verify Neo4j graph data
```cypher
-- All nodes
MATCH (n:Entity) RETURN n LIMIT 25

-- All relationships
MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity)
RETURN a.name, r.type, b.name LIMIT 25
```