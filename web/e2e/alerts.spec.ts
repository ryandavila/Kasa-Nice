import { test, expect } from '@playwright/test';

// Alerts e2e: set a power-draw threshold below a metered fake device's steady
// draw and watch the bell badge/dropdown pick up the resulting "power_exceeded"
// alert. The `just e2e` server runs the real alert evaluator and energy
// recorder against the fake registry (see api/main.py's KASA_FAKE_DEVICES
// branch and api/testing/fake_devices.py), both on a 10s interval set by the
// `e2e` justfile recipe — fast enough to observe a real evaluator cycle
// without inflating this spec's runtime much.
//
// "Kitchen Strip" is seeded with a constant 150W draw specifically so the
// rising edge fires deterministically the very first cycle after the
// threshold is set below it, with no need to control the device's wattage
// mid-test (see fake_devices.py's _sample_devices docstring).
test('a power threshold crossing shows up as a bell badge and alert entry', async ({ page }) => {
	await page.goto('/');

	const bell = page.getByRole('button', { name: 'Alerts' });
	await bell.click();

	// Set a threshold well below Kitchen Strip's steady 150W draw.
	const thresholdInput = page.getByLabel('Power threshold for Kitchen Strip in watts');
	await thresholdInput.fill('100');
	await thresholdInput.blur();
	// The store applies thresholds optimistically then persists; wait for the
	// persisted value so the reload below can't race the PUT.
	await expect(thresholdInput).toHaveValue('100');

	// Close the dropdown; the alert store's own 30s poll is too slow for a test,
	// so reload the page instead — its onMount calls startPolling(), which loads
	// once immediately, giving each retry below a fresh read of the ring buffer.
	await page.keyboard.press('Escape');

	await expect(async () => {
		await page.reload();
		await page.getByRole('button', { name: 'Alerts' }).click();
		await expect(page.getByText(/Kitchen Strip is drawing/)).toBeVisible({ timeout: 1_000 });
	}).toPass({ timeout: 30_000 });

	// The bell badge counts unseen alerts; opening the dropdown above (inside
	// toPass) already called markSeen(), so a fresh reload's badge is gone.
	await page.reload();
	const badge = bell.locator('span.bg-red-500');
	await expect(badge).toHaveCount(0);

	// Dropdown contents: the alert's message and "High power" label appear.
	await bell.click();
	await expect(page.getByText(/Kitchen Strip is drawing/)).toBeVisible();
	await expect(page.getByText('High power')).toBeVisible();

	// Clean up: clear the threshold so this spec leaves no state behind for
	// others sharing the server process.
	await thresholdInput.fill('');
	await thresholdInput.blur();
	await expect(thresholdInput).toHaveValue('');
});
