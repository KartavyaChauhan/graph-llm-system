# Graph LLM System (Order-to-Cash)

End-to-end graph-based analytics system for SAP-style Order-to-Cash (O2C) data, with:

- data ingestion and schema normalization
- graph construction over business entities
- deterministic query engine (planner -> executor -> formatter)
- LLM-powered natural-language intent parsing with guardrails
- FastAPI backend and React graph + chat frontend

---

## 1) What this project does

Business flow data is usually fragmented across many tables and exports (sales orders, deliveries, billing, FI, customers, products).  
This project unifies those records into a graph so users can:

- visualize entity relationships
- run natural-language queries
- receive structured, data-backed answers
- highlight related nodes in the graph

This is not a static chatbot. The runtime pipeline is:

`user text -> constrained intent parser -> structured execution plan -> deterministic query execution -> answer synthesis`

---

## 2) Architecture overview

### Backend (`backend/`)

- `app/db/`
  - `schema.py`: canonical Pydantic models for SAP JSONL entities
  - `loader.py`: JSONL ingestion, normalization, dedupe/merge, `O2CDataBundle`

- `app/graph/`
  - `types.py`: node/edge enums + id helpers
  - `store.py`: graph store abstraction + NetworkX implementation
  - `graph_builder.py`: graph projection from loaded bundle
  - `manager.py`: traversal/lifecycle APIs for query layer

- `app/query/`
  - `types.py`: intent identifiers + execution plan contracts
  - `planner.py`: intent -> validated structured plan
  - `executor.py`: deterministic execution on bundle + graph
  - `formatter.py`: JSON-safe success/error payload shaping
  - `engine.py`: plan -> execute -> format facade

- `app/llm/`
  - `parser.py`: constrained NL-to-intent translation + post-validation
  - `openai_provider.py`: OpenAI-compatible provider adapter
  - providers supported via env: `gemini` (default), `groq`, `openrouter`

- `app/api/`
  - routers for `meta`, `graph`, `query`
  - request-id middleware and structured response schemas
  - deterministic answer generation service

- `app/main.py`
  - FastAPI app factory + lifespan warmup + CORS

### Frontend (`frontend/`)

- Vite + React + Tailwind
- split-screen UI:
  - left: interactive graph (`react-force-graph-2d`)
  - right: chat panel
- `src/services/api.js` handles:
  - graph fetch + transform to force-graph format
  - query calls (`POST /query`)
  - node highlight extraction from result payloads

---

## 3) Data model (graph)

### Node types

- `Order`
- `Delivery`
- `Invoice`
- `Payment`
- `JournalEntry`
- `Customer`
- `Product`
- `Address`

### Edge types

- `CUSTOMER_PLACED_ORDER`
- `ORDER_HAS_DELIVERY`
- `DELIVERY_HAS_INVOICE`
- `INVOICE_HAS_PAYMENT`
- `INVOICE_HAS_JOURNAL_ENTRY`
- `ORDER_CONTAINS_PRODUCT`

This supports tracing across O2C including billing-to-journal relationships where source linkage exists.

---

## 4) Query capabilities

Supported intents:

- `find_top_products_by_billing`
- `trace_order_flow`
- `find_incomplete_orders`
- `trace_billing_flow`

The LLM is restricted to intent and parameter extraction only. Execution and answer content are generated from data-backed deterministic logic.

---

## 5) API endpoints

Base URL (default local): `http://127.0.0.1:8000`

### Meta

- `GET /` -> service metadata and endpoint index
- `GET /health` -> health check
- `GET /llm/models` -> active provider/model diagnostics

### Graph

- `GET /graph`
  - query params:
    - `max_nodes` (default 8000)
    - `max_edges` (default 100000)
    - `metadata_max_chars` (default 400)
  - response includes `nodes`, `edges`, `stats`, `truncated`, `warnings`

### Query

- `POST /query`
  - body:
    ```json
    { "query": "Trace billing 91150187" }
    ```
  - response:
    - `answer`: concise deterministic summary
    - `data`: parse + query payload + result
    - `trace`: timings, intent, interpretation, errors

- `POST /query/structured`
  - body:
    ```json
    {
      "intent": "trace_order_flow",
      "parameters": { "order_id": "740506" }
    }
    ```

---

## 6) Environment configuration

Create `backend/.env` from `backend/.env.example`.

### Provider selection

- `LLM_PROVIDER=gemini` (default)
- `LLM_PROVIDER=groq`
- `LLM_PROVIDER=openrouter`

### Provider keys

- Gemini:
  - `GEMINI_API_KEY`
  - optional: `GEMINI_MODEL`, `GEMINI_MODEL_FALLBACKS`

- Groq:
  - `GROQ_API_KEY`
  - optional: `GROQ_MODEL`, `GROQ_BASE_URL`

- OpenRouter:
  - `OPENROUTER_API_KEY`
  - optional: `OPENROUTER_MODEL`, `OPENROUTER_BASE_URL`, `OPENROUTER_HTTP_REFERER`, `OPENROUTER_APP_TITLE`

### CORS

- `CORS_ORIGINS` (comma-separated) for non-dev deployments

---

## 7) Local setup and run

## 7.1 Backend

From repository root:

```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Backend docs:  
`http://127.0.0.1:8000/docs`

## 7.2 Frontend

In a new terminal:

```bash
cd frontend
npm install
npm run dev
```

Frontend local URL is typically:
`http://127.0.0.1:5173` or `http://localhost:5173`

Optional env in frontend:

- `VITE_API_BASE=http://127.0.0.1:8000`

---

## 8) Deployment guide (Vercel + Render)

This project is split-deployed:

- **Frontend:** Vercel (Vite React app)
- **Backend:** Render (FastAPI service)

### Recommended production URLs

- Frontend (stable custom project domain):  
  `https://graph-llm-system.vercel.app`
- Backend (Render web service):  
  `https://graph-llm-system-qixc.onrender.com`

### Why this split

- Vercel gives fast static frontend hosting and straightforward environment injection for Vite.
- Render runs the Python API service and supports environment-managed configuration and redeploys.

### Frontend deployment options

- **Option A (recommended): Vercel**
  - Root directory: `frontend`
  - Build command: `npm run build`
  - Output directory: `dist`
  - Env var: `VITE_API_BASE=https://graph-llm-system-qixc.onrender.com`

- **Option B (alternative): Netlify / Cloudflare Pages**
  - Same Vite build settings
  - Equivalent frontend env var still required (`VITE_API_BASE`)

### Backend deployment options

- **Option A (recommended): Render**
  - Runtime: Python
  - Start command example: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  - Root directory: `backend`
  - Env vars:
    - `LLM_PROVIDER=gemini` (or groq/openrouter)
    - provider API key(s)
    - `CORS_ORIGINS=https://graph-llm-system.vercel.app`

- **Option B (alternative): Railway / Fly.io / Azure App Service**
  - Same FastAPI startup and env model
  - Ensure CORS includes your frontend origin

### Production env checklist

#### Vercel
- `VITE_API_BASE=https://graph-llm-system-qixc.onrender.com`

#### Render
- `CORS_ORIGINS=https://graph-llm-system.vercel.app`
- `LLM_PROVIDER=<gemini|groq|openrouter>`
- matching provider key:
  - `GEMINI_API_KEY` or
  - `GROQ_API_KEY` or
  - `OPENROUTER_API_KEY`

### Post-deploy verification

1. Open backend health:
   - `https://graph-llm-system-qixc.onrender.com/health`
2. Open frontend:
   - `https://graph-llm-system.vercel.app`
3. In browser network tab, confirm graph/query requests go to Render URL (not `127.0.0.1`).
4. Test chat with:
   - `Which products are billed the most?`
   - `Trace the order flow for order 740506`
   - `Trace billing 91150187`

### Important deployment note

Render free instances can spin down after inactivity and may take time to wake (cold start).  
This can delay first request responses but should not break functionality.

---

## 9) Verification checklist

After startup, validate in this order:

1. `GET /health` returns `{"status":"ok"}`
2. `GET /llm/models` shows active provider and `llm_configured: true`
3. `GET /graph` returns non-empty `nodes` and `edges`
4. In chat UI, test:
   - `Which products are billed the most?`
   - `Trace the order flow for order 740506`
   - `Trace billing 91150187`
   - `Show incomplete orders delivered but not billed`
5. Confirm node highlights appear for successful trace-style queries

---

## 10) Guardrails and reliability notes

- off-topic prompts are rejected
- unsupported intents do not execute
- unknown/malformed parameters are sanitized or rejected
- answers are generated from query result payloads, not free-form model output
- graph export supports limits to avoid overlarge visualization payloads

---

## 11) Current scope and known boundaries

- no authentication/authorization layer
- no rate limiting by default
- no streaming token responses
- no conversational memory across chat turns
- quality of some flow links depends on source data completeness

These are reasonable trade-offs for assignment scope and can be extended incrementally.

---

## 12) Challenges faced

- **Fragmented references across exports:** Billing, delivery, and FI datasets do not always share one clean foreign key path. Some records reference delivery IDs, others reference order IDs or FI document IDs.
- **Inconsistent identifier formats:** Item/document identifiers can appear with different padding and formatting, requiring normalization before join logic is reliable.
- **Large graph rendering trade-offs:** Raw graph payloads can become heavy for browser rendering, so export limits and metadata truncation were added to keep the UI responsive.
- **LLM reliability vs control:** Natural-language input is flexible, but execution must remain deterministic and safe. This required strict intent schemas, allow-listed parameters, and explicit rejection paths.

## 13) Architectural decisions

- **Separation of concerns (`planner -> executor -> formatter`):** Intent interpretation is isolated from execution logic and response shaping, which improves debuggability and makes behavior easier to test.
- **Graph-first business traversal:** O2C lifecycle questions map naturally to typed graph traversals, reducing repeated multi-table join complexity at query time.
- **Deterministic answer layer:** The LLM is used only for intent parsing; final answers are generated from query results to avoid hallucinated claims.
- **Provider abstraction for LLM parsing:** Provider-specific calls are isolated behind a shared translator interface, enabling Gemini/Groq/OpenRouter support without changing API contracts.
- **Backend-driven trace metadata:** `trace` and interpretation payloads are returned with each query to support auditability and easier troubleshooting.

---

## 14) Design notes (required rationale)

### Architecture decisions

- The system is split into clear layers: ingestion (`db`) -> graph modeling (`graph`) -> deterministic query execution (`query`) -> API contracts (`api`) -> UI.
- Planner/executor separation keeps natural-language interpretation decoupled from data execution, improving observability and maintainability.
- The graph manager is exposed through a store abstraction, so traversal logic is stable even if storage implementation changes later.

### Database/storage choice

- This implementation uses **in-memory typed storage**:
  - canonical records in `O2CDataBundle` dictionaries
  - relationship graph in **NetworkX** (`NetworkXGraphStore`)
- Why this choice:
  - fast iteration for assignment scope
  - direct support for relationship traversal queries (order lifecycle, path tracing)
  - low operational overhead compared to introducing external databases for a take-home project
- Trade-off: no persistence/auth/indexing layer yet (acceptable for assignment scope; straightforward to extend).

### LLM prompting strategy

- LLM is used only for **intent + parameter extraction**, never as the final source of business facts.
- Prompting is constrained with:
  - explicit supported-intent list
  - strict JSON output schema
  - reject examples for off-topic prompts
  - low-temperature generation and post-validation
- Output is normalized and allow-listed before query execution.

### Guardrails

- Domain restriction: non-O2C prompts are rejected.
- Execution safety: only allow-listed intents execute.
- Parameter safety: unknown/malformed fields are stripped or rejected.
- Response safety: final answers are generated from query results, not free-form model narration.

---

## 15) Repository layout

```text
graph-llm-system/
  backend/
    app/
      api/
      db/
      graph/
      llm/
      query/
      main.py
    data/raw/sap-o2c-data/
    requirements.txt
  frontend/
    src/
      components/
      services/
    package.json
  README.md
```

