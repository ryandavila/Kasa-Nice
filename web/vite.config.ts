import tailwindcss from '@tailwindcss/vite';
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// SPA mode: a single fallback page handles all client-side routes. The
			// FastAPI backend serves this build and owns everything under /api.
			adapter: adapter({ fallback: 'index.html' })
		})
	],
	server: {
		// In dev, forward API calls to the FastAPI backend so frontend code can
		// always fetch relative '/api/...' paths in both dev and production.
		// `just dev <port>` overrides the target so dev can dodge a busy 8080.
		proxy: {
			'/api': {
				target: process.env.API_PROXY_TARGET ?? 'http://localhost:8080',
				changeOrigin: true
			}
		}
	}
});
