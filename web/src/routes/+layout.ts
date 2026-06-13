// This app is a client-rendered SPA backed by the FastAPI service. Disable SSR
// so device state is always fetched live in the browser, and prerender the
// single fallback shell that the backend serves for every route.
export const ssr = false;
export const prerender = false;
