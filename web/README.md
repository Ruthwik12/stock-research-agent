# Web UI

React 19 + TypeScript + Vite front end for the Stock Research Agent. It talks only to the FastAPI backend
(`api/main.py`); during development Vite forwards `/api/*` to `http://127.0.0.1:8000` (see `vite.config.ts`).

```powershell
npm install
npm run dev      # http://localhost:5173 (start the backend first)
npm run build    # type-check and build to dist/
```

- `src/App.tsx`: tabs Today's picks, Positions, Shortlist, Research, Reflector
- `src/Traces.tsx`: the Traces tab (replays a saved run step by step)
- `src/api.ts`: types and calls for every backend endpoint

How to use each screen: [../docs/USER_MANUAL.md](../docs/USER_MANUAL.md).
