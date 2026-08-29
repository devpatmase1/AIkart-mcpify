# ⚡ MCPify

> **Analyze any AI agent URL, detect framework capabilities, and generate ready-to-use Model Context Protocol (MCP) configurations.**
> MCPify also exposes its own `/mcp` endpoint so it can itself be added as an MCP connector to any MCP client (Claude Desktop, Cursor, VS Code, etc.).

---

## 🚀 Features

- 🔍 **Universal Agent URL Analysis**: Probes common endpoints (`/mcp`, `/sse`, `/health`, `/tools`, `/docs`, `/openapi.json`) concurrently with HTTP heuristics.
- 🧠 **Framework Detection**: Identifies FastAPI, Flask, LangChain/LangGraph, Express, and Next.js agents with confidence scoring.
- ⚙️ **Instant MCP Config Generator**: Outputs ready-to-copy configuration JSON for:
  - **Claude Desktop** (`mcpServers` format)
  - **Cursor & VS Code** (SSE/Remote MCP format)
  - **Remote MCP Endpoint**
- 🔌 **Self-Hosting MCP Server**: Powered by `fastapi-mcp`, exposing its own `/mcp` endpoint and tools directly to AI clients.
- ⏱️ **Zero-Downtime Free Tier Keep-Alive**: Integrated APScheduler keep-alive worker pinging `/health` every 10 minutes to prevent Render/Koyeb sleeping.
- 🌐 **Full CORS Support**: Open for web frontends, browser extensions, and developer portals.

---

## 📁 Project Structure

```
mcpify/
├── main.py              # FastAPI app entry point with FastApiMCP & APScheduler
├── requirements.txt     # Python dependencies
├── render.yaml          # Render deployment blueprint
├── .env.example         # Sample environment configuration
├── README.md            # Project documentation & guides
└── app/
    ├── __init__.py      # Package initialization
    ├── analyzer.py      # URL probing & framework detection heuristics
    ├── generator.py     # MCP configuration JSON generator
    └── mcp_handler.py   # MCP tools definition & REST router
```

---

## 🛠️ MCP Tools Exposed

When connected via `/mcp`, MCPify exposes three primary tools:

| Tool Name | Parameters | Description |
| :--- | :--- | :--- |
| `analyze_agent` | `url` (string) | Probes agent endpoints, detects framework, returns confidence score and accessible routes. |
| `generate_mcp_config` | `url` (string) | Analyzes agent and generates complete Claude Desktop and Cursor JSON configs. |
| `get_integration_guide` | `url` (string), `platform` (`claude_desktop` \| `cursor` \| `web`) | Step-by-step instructions for adding the agent to your client. |

---

## 💻 Local Setup & Installation

### 1. Clone & Navigate
```bash
git clone <your-repo-url>
cd mcpify
```

### 2. Create Virtual Environment
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment
```bash
cp .env.example .env
```
Edit `.env`:
```env
APP_URL=http://localhost:10000
```

### 5. Run the Server
```bash
uvicorn main:app --host 0.0.0.0 --port 10000 --reload
```
- Interactive Swagger API Docs: `http://localhost:10000/docs`
- MCP Endpoint: `http://localhost:10000/mcp`
- Health Check: `http://localhost:10000/health`

---

## 🚢 Deploying to Render

This project includes a preconfigured `render.yaml` for 1-click deployment on Render's free tier.

1. Push this repository to GitHub or GitLab.
2. Log into [Render Dashboard](https://dashboard.render.com/).
3. Click **New +** > **Blueprint**.
4. Connect your repository.
5. Set `APP_URL` in the environment variables to your assigned Render URL (e.g. `https://mcpify.onrender.com`).
6. Deploy! The built-in APScheduler will automatically keep your service awake.

---

## 🔌 Connecting MCPify to Your Client

### 1. Claude Desktop
Add MCPify to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "mcpify": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://your-mcpify-url.onrender.com/mcp"
      ]
    }
  }
}
```

### 2. Cursor / VS Code
In **Cursor Settings > Features > MCP > Add New MCP Server**:
- **Name**: `mcpify`
- **Type**: `sse`
- **URL**: `https://your-mcpify-url.onrender.com/mcp`

---

## 📡 API Endpoints

### 1. Health Check
`GET /health`
```json
{
  "status": "ok"
}
```

### 2. Analyze Agent URL
`POST /analyze`
```json
{
  "url": "https://sample-agent.onrender.com"
}
```
**Response:**
```json
{
  "url": "https://sample-agent.onrender.com",
  "detected_framework": "FastAPI",
  "confidence_score": 0.95,
  "available_endpoints": [
    {
      "path": "/health",
      "status_code": 200,
      "accessible": true,
      "content_type": "application/json"
    },
    {
      "path": "/docs",
      "status_code": 200,
      "accessible": true,
      "content_type": "text/html; charset=utf-8"
    }
  ],
  "recommended_mcp_endpoint": "https://sample-agent.onrender.com/mcp",
  "details": {
    "signals": [
      "Server header contains 'uvicorn'",
      "OpenAPI specification available at /openapi.json",
      "Swagger UI documentation available at /docs"
    ],
    "framework_scores": {
      "FastAPI": 0.95,
      "Flask": 0.0,
      "LangChain": 0.0,
      "Express": 0.0,
      "Next.js": 0.0
    }
  }
}
```

### 3. Generate MCP Config
`POST /generate`
```json
{
  "url": "https://sample-agent.onrender.com"
}
```

### 4. Integration Guide
`POST /guide`
```json
{
  "url": "https://sample-agent.onrender.com",
  "platform": "claude_desktop"
}
```

---

## 📜 License
MIT License. Built for seamless AI agent interoperability with Model Context Protocol.
