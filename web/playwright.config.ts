import { defineConfig, devices } from '@playwright/test';

// End-to-end smoke test config. The server lifecycle is owned by the `just e2e`
// recipe (it builds the SPA, starts the API with KASA_FAKE_DEVICES=1, waits for
// it, and tears it down), so this config only points the browser at that
// production-style server.
const baseURL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8080';

export default defineConfig({
	testDir: 'e2e',
	// The SSE live-update assertion waits for a server-driven change, so give the
	// whole spec generous headroom over the stream's re-read interval.
	timeout: 60_000,
	fullyParallel: false,
	forbidOnly: !!process.env.CI,
	retries: 0,
	// `list` avoids spawning the HTML report server (which would hang CI), and
	// artifacts land under the gitignored build dir so they never trip `just ci`.
	reporter: 'list',
	outputDir: 'build/.playwright',
	use: {
		baseURL,
		trace: 'retain-on-failure'
	},
	projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }]
});
