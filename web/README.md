# Kasa-Nice web

The SvelteKit frontend for Kasa-Nice — a single-page app for discovering and
controlling TP-Link Kasa devices. It talks to the FastAPI backend (`../api`)
over a small REST API and polls for live device state.

Built with Svelte 5 (runes), Tailwind CSS v4, and `@sveltejs/adapter-static`.

## Develop

Install dependencies and start the dev server (Vite, port 5173):

```sh
bun install
bun run dev
```

The dev server proxies `/api/*` to the backend at `http://localhost:8080`
(see [`vite.config.ts`](vite.config.ts)), so run the backend alongside it:

```sh
# from the repo root
uv run python -m api.main
```

## Build

```sh
bun run build      # outputs a static SPA to ./build
bun run preview    # preview the production build
```

In production the FastAPI backend serves `web/build` and owns everything under
`/api`. The project `Dockerfile` builds this step automatically.

## Checks

```sh
bun run check      # svelte-check (type + a11y)
bun run lint       # prettier + eslint
bun run format     # apply prettier
```

## Layout

- `src/routes` — pages (`+page.svelte` is the whole app: Devices, Energy, Discovery)
- `src/lib/components` — UI components (device cards, charts, controls)
- `src/lib/stores` — runes-based state (`devices`, `theme`, `toasts`)
- `src/lib/api` — typed client and request/response types for the backend
