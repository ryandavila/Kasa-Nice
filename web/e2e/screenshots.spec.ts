import { test, expect } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// README hero screenshots: light and dark captures of the Devices and Energy
// tabs, using the same realistic seeded fake devices as the other e2e specs
// (see api/testing/fake_devices.py) — "Living Room Lamp", "Reading Bulb",
// "Porch Light", "Office Desk", and "Kitchen Strip" read as a believable home,
// not test fixtures. Excluded from the pass/fail `just e2e` run via the
// `screenshots` Playwright project (see playwright.config.ts); regenerate with
// `just screenshots`. No assertions beyond "the tab has rendered" — this spec's
// job is the PNGs it writes, not correctness (that's what the other specs are for).

const OUT_DIR = path.resolve(
	fileURLToPath(import.meta.url),
	'..',
	'..',
	'..',
	'docs',
	'screenshots'
);

// A fixed desktop size renders the two-column card grid the hero images want,
// regardless of the machine running the capture.
test.use({ viewport: { width: 1440, height: 900 } });

async function setTheme(page: import('@playwright/test').Page, mode: 'light' | 'dark') {
	await page.evaluate((m) => {
		document.documentElement.dataset.theme = m;
		try {
			localStorage.setItem('kasa-theme', m);
		} catch {
			/* ignore */
		}
	}, mode);
}

for (const mode of ['light', 'dark'] as const) {
	test(`captures the Devices and Energy tabs (${mode})`, async ({ page }) => {
		await page.goto('/');
		await setTheme(page, mode);
		await expect(page.getByRole('heading', { name: 'Living Room Lamp' })).toBeVisible();
		// Porch Light flips on its own read; settle on a stable, on-looking scene
		// so the two captures of a run don't differ by device state.
		const porchSwitch = page.getByRole('switch', { name: 'Toggle Porch Light' });
		if (!(await porchSwitch.isChecked())) {
			await porchSwitch.click();
			await expect(porchSwitch).toBeChecked();
		}
		// Let the "energized wash" animation/transition on cards settle before
		// capturing, so the screenshot isn't mid-fade. fullPage so the capture
		// isn't cropped to the viewport fold — both tabs render more content than
		// fits in 900px tall.
		await page.waitForTimeout(600);
		await page.screenshot({ path: path.join(OUT_DIR, `devices-${mode}.png`), fullPage: true });

		await page.getByRole('button', { name: 'Energy' }).click();
		await expect(page.getByRole('heading', { name: 'Whole home' })).toBeVisible();
		// Per-device energy cards load asynchronously (one fetch per meter);
		// wait for the last metered device's numbers to replace its skeleton.
		await expect(page.getByRole('heading', { name: 'Kitchen Strip' })).toBeVisible();
		await page.waitForTimeout(300);
		await page.screenshot({ path: path.join(OUT_DIR, `energy-${mode}.png`), fullPage: true });
	});
}
