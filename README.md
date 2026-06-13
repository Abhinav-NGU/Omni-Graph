# OmniGraph

OmniGraph is a production-grade, self-hosted RAG platform designed for
high-accuracy, low-latency conversational AI. It integrates a sophisticated
agentic workflow with a dual-representation knowledge base (vector + graph) to
deliver fast, accurate, and context-aware answers.

The system features a LangGraph-powered agent that intelligently routes queries, performs hybrid search and contextual compression for optimal context retrieval, and streams responses in real-time. With a complete microservices stack including Neo4j, Qdrant, and Ollama, plus a full-featured chat UI, OmniGraph provides a robust foundation for building and exploring complex knowledge domains.

## Status: Phase 7 Complete ✅

---

## Architecture

| Service | Technology | Purpose |
|---|---|---|
| **Traefik** | Go | API gateway — routes all incoming requests |
| **Qdrant** | Rust | Vector database — semantic chunk search |
| **Neo4j** | Java | Graph database — entity relationship storage |
| **Ollama** | Go | Local LLM engine (llama3.2 + nomic-embed-text) |
| **Redis** | C | Session storage — persists conversation history |
| **Jaeger** | Go | Distributed tracing and observability |
| **orchestration-service** | Python/FastAPI | Core service — all pipelines and agent logic |
| **frontend** | Next.js | Chat UI with debug panels and session sidebar |
| **mcp-tools-service** | Go/Fiber | External tools for the agent |

---

## What's Been Built

### Phase 1 — Infrastructure
- Fully containerized stack via Docker Compose
- Traefik API gateway, Jaeger tracing
- Neo4j, Qdrant, Ollama, Redis services
- Configuration management via `.env` + `pydantic-settings`
- Database connection lifecycle with tenacity retry logic
- Deep health checks at `/health`

### Phase 2 — RAG Pipeline
- `/ingest` — text chunking, embedding (nomic-embed-text), 
  knowledge graph extraction (llama3.2), dual storage to Qdrant + Neo4j
- Content-hash deduplication — safe to re-ingest the same document
- `/query` — semantic search + graph context + LLM synthesis
- `/ingest/pdf` — PDF file upload and ingestion
- `/ingest/url` — webpage fetch and ingestion
- `/collections/clear` + `/graph/clear` — admin wipe endpoints

### Phase 3 — LangGraph Agent
- `/chat` — agentic multi-turn conversation
- Smart routing: agent picks vector / graph / both based on question type
- Retry logic: escalates strategy if context is insufficient (max 2 retries)
- Redis-backed session storage — history survives restarts
- `/chat/{id}/history` — fetch conversation history
- `DELETE /chat/{id}` — clear a session
- `/chat/sessions` — list all active sessions

### Phase 4 — Production Hardening
- Redis persistent session storage
- PDF ingestion (`/ingest/pdf`)
- URL ingestion (`/ingest/url`)
- API key authentication on all protected endpoints (`X-API-Key` header)
- `/graph/clear` admin endpoint

### Phase 5 — Frontend
- Dark command-center aesthetic (JetBrains Mono + DM Sans)
- API key login gate
- Session sidebar — topic summary, preview, message count, time ago, session ID
- Sessions persist across browser refreshes (loaded from Redis on mount)
- Per-message debug panel — reasoning trace, source chunks with score bars,
  graph paths
- Ingest modal — text / PDF / URL tabs
- Real-time health status bar (Neo4j, Qdrant, Redis, Ollama)
- Scroll controls, quick prompt suggestions, animated typing indicator

### Phase 6a — Neo4j Optimisation
- Fulltext index on `Entity.name` — 10-100x faster graph queries
- Entity name normalisation before write — prevents duplicate nodes
  from casing differences ("Elon Musk" vs "elon musk")
- Relationship type normalisation to UPPER_SNAKE_CASE

### Phase 6b — Semantic Chunking
- Replaced fixed-size character chunking with topic-aware splitting
- Embeds sentences and finds topic boundaries via cosine similarity drops
- Chunks contain complete thoughts — no sentences split mid-fact
- Configurable similarity threshold, min/max chunk sizes

### Phase 6c — Hybrid Search
- BM25 keyword search + vector semantic search run in parallel
- Results merged with Reciprocal Rank Fusion (RRF)
- Chunks appearing in both lists bubble to top — fixes name/keyword matching
- Directly fixed "Abhinav Bindra scores lower than Elon Musk" problem

### Phase 6d — Contextual Compression
- After retrieval, LLM extracts only relevant sentences from each chunk
- Reduces context passed to final LLM by 60-90%
- Drops chunks with no relevant content entirely
- Sharper, more accurate answers with less hallucination

### Phase 6e — Response Streaming
- `/chat/stream` endpoint streams tokens via Server-Sent Events
- Frontend renders tokens as they arrive — no more waiting 15s for silence
- Streams reasoning steps, sources, graph paths, then tokens in order
- Falls back gracefully on network errors

### Phase 7 — Advanced Agent Tools & Chat Uploads
- `/chat/upload` endpoint injects PDFs directly into the agent's conversation memory.
- Background-threaded PDF parsing prevents blocking the FastAPI event loop.
- **Active LLM Grader**: Enforces strict entity matching to prevent hallucinations on partial names.
- **Ambiguity Resolution**: Agent proactively asks for clarification on vague queries.
- **Agent Tools**: Programmatic timezone conversion and dynamic web search fallback for missing data.
- **UI Enhancements**: One-shot file attachments, document rendering in chat bubbles, and silent API key auto-restore.

---

## API Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | ✗ | Service info |
| GET | `/health` | ✗ | Deep health check |
| POST | `/ingest` | ✅ | Ingest raw text |
| POST | `/ingest/pdf` | ✅ | Upload PDF |
| POST | `/ingest/url` | ✅ | Fetch and ingest URL |
| POST | `/query` | ✅ | Simple RAG query |
| POST | `/chat` | ✅ | Agentic chat with memory |
| POST | `/chat/stream` | ✅ | Streaming agentic chat (SSE) |
| POST | `/chat/upload` | ✅ | Upload PDF directly into chat session |
| GET | `/chat/sessions` | ✅ | List all sessions |
| GET | `/chat/{id}/history` | ✅ | Get session history |
| DELETE | `/chat/{id}` | ✅ | Clear a session |
| POST | `/collections/clear` | ✅ | Wipe Qdrant collection |
| POST | `/graph/clear` | ✅ | Wipe Neo4j graph |

Full docs at `http://localhost:8000/docs`

---

## Getting Started

### 1. Prerequisites
- Docker Desktop (or Docker Engine + Docker Compose)
- *(Optional)* NVIDIA Container Toolkit + WSL2 Ubuntu for GPU acceleration

### 2. Configure `.env`

```dotenv
COMPOSE_PROJECT_NAME=omnigraph

NEO4J_USER=neo4j
NEO4J_PASSWORD=yourSuperSecurePassword!

NEO4J_URI=bolt://neo4j:7687
QDRANT_URL=http://qdrant:6333
OLLAMA_BASE_URL=http://ollama:11434
REDIS_URL=redis://redis:6379

API_KEY=your-secret-api-key

JAEGER_AGENT_HOST=jaeger
JAEGER_AGENT_PORT=6831
```

### 3. Build and Run

```bash
docker-compose up --build -d
```

### 4. Pull Ollama Models (first time only)

```bash
docker exec -it ollama ollama pull nomic-embed-text
docker exec -it ollama ollama pull llama3.2
```

### 5. Create Neo4j Index (first time only)

Open `http://localhost:7474` and run:

```cypher
CREATE FULLTEXT INDEX entity_name_index IF NOT EXISTS
FOR (e:Entity) ON EACH [e.name];
```

### 6. Verify

```bash
docker-compose ps
# All services should be running or healthy
```

---

## Service URLs

| Service | URL |
|---|---|
| Chat UI | http://localhost:3000 |
| API + Swagger | http://localhost:8000/docs |
| Traefik Dashboard | http://localhost:8080 |
| Qdrant Dashboard | http://localhost:6333/dashboard |
| Neo4j Browser | http://localhost:7474 |
| Jaeger UI | http://localhost:16686 |
| Ollama API | http://localhost:11434 |

---

## Usage

### Ingest a document
```bash
# Text
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"text": "Abhinav Bindra won gold at the 2008 Beijing Olympics."}'

# URL
curl -X POST http://localhost:8000/ingest/url \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"url": "https://en.wikipedia.org/wiki/Abhinav_Bindra"}'
```

### Chat (streaming)
```bash
curl -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"question": "Who is Abhinav Bindra?"}' \
  --no-buffer
```

### Verify graph data
```cypher
MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity)
RETURN a.name, r.type, b.name LIMIT 25
```

---

## Roadmap

| Phase | Status | Description |
|---|---|---|
| 1 | ✅ | Infrastructure |
| 2 | ✅ | RAG Pipeline |
| 3 | ✅ | LangGraph Agent |
| 4 | ✅ | Production Hardening |
| 5 | ✅ | Frontend |
| 8a | ✅ | Neo4j Index + Entity Normalisation |
| 8b | ✅ | Semantic Chunking |
| 8c | ✅ | Hybrid Search (BM25 + Vector + RRF) |
| 8d | ✅ | Contextual Compression |
| 8e | ✅ | Response Streaming |
| 9 | ✅ | Advanced Agent Tools & Chat Uploads |
| 6 | ✅ | mcp-tools-service (Go/Fiber) |
| 7 | 🔄 | Observability (OpenTelemetry + Jaeger) |