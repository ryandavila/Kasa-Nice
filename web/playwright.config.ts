import { defineConfig, devices } from '@playwright/test';

// End-to-end smoke test config. The server lifecycle is owned by the `just e2e`
// recipe (it builds the SPA, starts the API with KASA_FAKE_DEVICES=1, waits for
// it, and tears it down), so this config only points the browser at that
// production-style server.
const baseURL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8080';

export default defineConfig({
	testDir: 'e2e',
	// The SSE live-update assertion waits for a server-driven change, so give the
	// whole spec headroom over the stream's re-read interval, and the alerts spec
	// headroom over an evaluator cycle.
	timeout: 60_000,
	fullyParallel: false,
	// All specs share ONE `just e2e` server/registry (see the recipe) — each
	// spec creates/cleans up its own uniquely-named data, but that isolation
	// only holds if specs run one at a time. Playwright still runs separate
	// FILES in parallel workers by default even with fullyParallel off, so pin
	// to a single worker to serialize every spec against the shared server.
	workers: 1,
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
	// Two projects so `just e2e`'s default run (all projects) never executes the
	// screenshot spec, while `just screenshots` can target it specifically with
	// `--project=screenshots`. screenshots.spec.ts asserts nothing pass/fail —
	// it just captures polished README images — so it must stay out of the
	// green/red e2e signal.
	projects: [
		{
			name: 'chromium',
			testIgnore: 'screenshots.spec.ts',
			use: { ...devices['Desktop Chrome'] }
		},
		{
			name: 'screenshots',
			testMatch: 'screenshots.spec.ts',
			use: { ...devices['Desktop Chrome'] }
		}
	]
});
