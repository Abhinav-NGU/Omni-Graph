# OmniGraph Frontend

A modern Next.js frontend for the OmniGraph chatbot and knowledge graph system.

## Features

- 🤖 AI-powered chat with multi-turn conversation support
- 📊 Real-time health monitoring of backend services
- 📝 Text ingestion interface
- 🌐 URL content ingestion
- 🔐 API key authentication
- 🎨 Dark-themed UI with Tailwind CSS
- 🔍 Debug panel for service status inspection

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

- **ChatWindow** — Main chat interface with multi-turn support
- **MessageBubble** — Individual message display with sources and reasoning
- **IngestPanel** — Text and URL ingestion forms
- **DebugPanel** — Backend service status inspection
- **HealthBar** — Real-time service health indicator

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
