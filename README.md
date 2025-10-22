# Code Explorer

FastAPI microservice for Git repository code indexing and RAG-based Q&A.

## Features

- **Git Repository Indexing** - Clone and index public/private repos with branch support
- **AST-Aware Chunking** - Tree-sitter parsing for Python, JavaScript, TypeScript, Go, Java, C#
- **Vector Search** - Pinecone serverless with versioned namespaces for atomic updates
- **RAG Chat** - Context-aware Q&A powered by OpenAI
- **Supabase Integration** - PostgreSQL database with Row Level Security + JWT auth
- **Async Task Queue** - ARQ + Redis for background indexing jobs
- **Webhook Notifications** - HMAC-signed callbacks for indexing status updates
- **Observability** - Structured logging, OpenTelemetry tracing, Prometheus metrics

## Requirements

- Python 3.13+
- Redis
- Supabase project (PostgreSQL + Auth)
- Pinecone serverless index
- OpenAI API key

## Quick Start

### 1. Install dependencies

```bash
# Using uv (recommended)
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Or with pip
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Set up Supabase

Run the schema in your Supabase SQL editor:

```bash
# Run supabase/schema.sql on Supabase SQL Editor
```

### 4. Start Redis

```bash
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

### 5. Run the API

```bash
uvicorn code_explorer.main:app --reload
```

### 6. Run the worker (separate terminal)

```bash
python -m code_explorer.workers.worker
```

## Docker Compose

```bash
cd docker

# Start all services
docker compose up -d

# With monitoring (Prometheus + Jaeger)
docker compose --profile monitoring up -d

# View logs
docker compose logs -f api worker
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/health/ready` | Readiness probe |
| GET | `/git/branches` | Discover branches for a Git URL |
| POST | `/repos` | Create and index a repository |
| GET | `/repos` | List user's repositories |
| GET | `/repos/{id}` | Get repository details |
| DELETE | `/repos/{id}` | Delete repository and vectors |
| POST | `/repos/{id}/reindex` | Trigger re-indexing |
| POST | `/chat` | Ask questions about a repository |
| GET | `/chat/history/{repo_id}` | Get chat history for a repository |
| DELETE | `/chat/history/{repo_id}` | Clear chat history for a repository |
| GET | `/metrics` | Prometheus metrics |

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Client    │────▶│  FastAPI    │────▶│  Supabase   │
└─────────────┘     │    API      │     │  PostgreSQL │
                    └──────┬──────┘     └─────────────┘
                           │
                    ┌──────▼──────┐
                    │    Redis    │
                    │  (ARQ Queue)│
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐     ┌─────────────┐
                    │  ARQ Worker │────▶│  Pinecone   │
                    │  (Indexing) │     │  (Vectors)  │
                    └──────┬──────┘     └─────────────┘
                           │
                    ┌──────▼──────┐
                    │   OpenAI    │
                    │ (Embeddings)│
                    └─────────────┘
```

## Configuration

See `.env.example` for all configuration options.

### Required

- `SUPABASE_URL` - Supabase project URL
- `SUPABASE_ANON_KEY` - Supabase anonymous key
- `SUPABASE_SERVICE_ROLE_KEY` - Supabase service role key
- `SUPABASE_JWT_SECRET` - JWT secret for token verification
- `DATABASE_URL` - PostgreSQL connection string
- `REDIS_URL` - Redis connection URL
- `PINECONE_API_KEY` - Pinecone API key
- `PINECONE_INDEX_NAME` - Pinecone index name
- `OPENAI_API_KEY` - OpenAI API key

### Optional

- `LOG_LEVEL` - Logging level (default: INFO)
- `LOG_FORMAT` - json or console (default: json)
- `OTLP_ENDPOINT` - OpenTelemetry collector endpoint
- `RATE_LIMIT_PER_USER` - Requests per user per window (default: 100)
- `RATE_LIMIT_PER_IP` - Requests per IP per window (default: 1000)

## Development

```bash
# Run linter
ruff check src tests

# Run type checker
mypy src

# Run tests
pytest

# Format code
ruff format src tests
```
