# JobHunt frontend

Modern UI for JobHunt — **Next.js (App Router) + TypeScript + Tailwind + Framer Motion + react-three-fiber (Three.js)**. It's a client-side SPA that talks to the FastAPI backend's REST API + `/ws/stream` WebSocket.

## Screens
- `/` — animated landing with a Three.js particle hero.
- `/dashboard` — stat cards, pipeline kanban, autonomy controls, live agent-reasoning feed, résumé preview drawer.
- `/onboarding` — paste-to-prefill résumé builder (editable Experience / Education / Projects / Skills / Links).

## Develop
```bash
cd frontend
npm install
# point at a running backend (python -m jobhunt serve, default :8000)
NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev   # http://localhost:3000/app
```
On the backend, allow the dev origin so cookies/fetches work:
```bash
JOBHUNT_CORS_ORIGINS=http://localhost:3000 python -m jobhunt serve
```

## Build (production)
```bash
cd frontend
npm ci
npm run build          # static export → frontend/out
```
The backend automatically serves `frontend/out` under **`/app`** when it exists
(`basePath`/`assetPrefix` are `/app`, so all routes/assets resolve there). Set
`JOBHUNT_FRONTEND_DIR` to serve an export from a custom path. The legacy SPA
stays at `/` during migration.

No backend changes are needed to ship the UI — it's all static, served by the
same FastAPI service (one deploy).
