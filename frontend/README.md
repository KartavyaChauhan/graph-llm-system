# O2C Graph + Query UI

React (Vite) + Tailwind + `react-force-graph` (2D). Talks to the FastAPI backend at `http://127.0.0.1:8000`.

## Prerequisites

- Backend running: from `../backend` run `uvicorn app.main:app --reload`
- Node.js 18+

## Setup

```bash
cd frontend
npm install
```

## Run

```bash
npm run dev
```

Open **http://localhost:5173**. CORS is enabled on the API for this origin.

## Optional env

Create `frontend/.env`:

```env
VITE_API_BASE=http://127.0.0.1:8000
```

## Build

```bash
npm run build
npm run preview
```
