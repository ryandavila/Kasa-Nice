import { svelte } from '@sveltejs/vite-plugin-svelte';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Unit-test config. Reuses the Svelte plugin so `.svelte.ts` rune modules (the
// stores) compile, and resolves the `$lib` alias the app code imports through.
// 'browser' resolve conditions pull in Svelte's client runtime so runes work
// outside a real component instance.
export default defineConfig({
	plugins: [svelte()],
	resolve: {
		alias: {
			$lib: fileURLToPath(new URL('./src/lib', import.meta.url))
		},
		conditions: ['browser']
	},
	test: {
		environment: 'jsdom',
		include: ['src/**/*.{test,spec}.ts']
	}
});
