# BioModStack Frontend

React + Vite UI for BioModStack.

## Run Locally

Full stack (API + UI):
```bash
./start_ui.sh
```

UI only:
```bash
cd platform/frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

The UI is served at `http://localhost:5173/bms/` (base path `/bms/`).
Requests to `/api` are proxied to `http://localhost:8000`.

## Build

```bash
npm run build
npm run preview
```

## Notes

- Base path is configured in `vite.config.ts` (`base: '/bms/'`).
- If you change the base path, update any reverse proxy or Tailscale Serve
  configuration to match.
