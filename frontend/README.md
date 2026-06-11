# OmniGraph Frontend

A modern Next.js frontend for the OmniGraph chatbot and knowledge graph system.

## Features
- **Real-time Conversational AI**: Streaming responses render as tokens arrive for a fluid user experience.
- **Full Session Management**: A persistent sidebar lists all conversations, allowing users to create, switch between, and delete sessions.
- **Advanced Ingestion**: A multi-tab modal for ingesting raw text, PDF files, or content from URLs, with visual feedback when tasks are queued.
- **Interactive Graph Explorer**: A dedicated modal to search for entities and visualize their connections in the knowledge graph with a force-directed layout.
- **Rich Debug Panel**: Per-message inspection panel showing the agent's reasoning trace, retrieved vector sources with relevance scores, and a visualizer for graph paths.
- **Persistent Authentication**: API key is stored securely in the browser's local storage with a 5-hour expiry, avoiding the need for re-entry on every visit.
- **Live Health Monitoring**: A top bar provides at-a-glance status of all backend services (Neo4j, Qdrant, Ollama).
- **Modern UI/UX**: A dark-themed, command-center aesthetic built with Tailwind CSS, featuring loading states, quick prompts, and scroll controls.
## Prerequisites

- Node.js 18+
- npm or yarn
- OmniGraph orchestration service running on `http://localhost:8000`

## Setup

1. **Clone the repository** (if not already done)
   ```bash
   cd frontend
   ```

2. **Install dependencies**
   ```bash
   npm install
   ```

3. **Configure environment variables**
   ```bash
   cp .env.example .env.local
   # Edit .env.local with your API URL and API Key
   ```

4. **Run development server**
   ```bash
   npm run dev
   ```

5. **Open in browser**
   ```
   http://localhost:3000
   ```

## Building for Production

```bash
npm run build
npm start
```

## Docker Deployment

```bash
docker build -t omnigraph-frontend .
docker run -p 3000:3000 \
  -e NEXT_PUBLIC_API_URL=http://your-api-url \
  -e NEXT_PUBLIC_API_KEY=your-api-key \
  omnigraph-frontend
```

## Components

- **ChatWindow**: The core component orchestrating the entire UI, including state management for sessions, messages, and API key persistence.
- **SessionSidebar**: Lists all conversations and handles session creation, selection, and deletion.
- **MessageBubble**: Renders individual user and assistant messages, and contains the collapsible `DebugPanel`.
- **DebugPanel**: Provides a detailed, per-message trace of the agent's reasoning, retrieved sources, and graph context.
- **IngestModal**: A multi-tab modal for ingesting content via text, PDF upload, or URL.
- **GraphExplorerModal**: An interactive tool for searching and visualizing the knowledge graph.
- **HealthBar**: A persistent header showing the real-time health of backend services.
## Environment Variables

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_API_URL` | OmniGraph API endpoint (default: `http://localhost:8000`) |
| `NEXT_PUBLIC_API_KEY` | API key for authentication |

## Tech Stack

- **Next.js 14** — React framework
- **TypeScript** — Type-safe development
- **Tailwind CSS** — Styling
- **React Hooks** — State management

## Notes

- API key is stored in browser localStorage for convenience (development only)
- For production, implement secure API key management
- All protected endpoints require the `X-API-Key` header
