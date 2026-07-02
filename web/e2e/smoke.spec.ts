import { test, expect } from '@playwright/test';

// One end-to-end smoke test that drives a real browser against the production
// build served by the API (with KASA_FAKE_DEVICES=1). It exercises the full
// wiring the unit tests can't: SPA -> REST -> registry, and the live SSE stream.
//
// The seeded fakes (see api/testing/fake_devices.py):
//   - "Living Room Lamp" — a plain plug, starts off, toggled by hand here.
//   - "Reading Bulb"      — a colour bulb, for card variety.
//   - "Porch Light"       — a plug that flips its own state on every server-side
//                           read, so the SSE stream pushes a change the browser
//                           never initiated (the live-update assertion below).
test('loads, renders cards, toggles a device, and reflects an SSE-driven change', async ({
	page
}) => {
	// 1. App loads.
	await page.goto('/');
	await expect(page).toHaveTitle(/Kasa Nice/i);

	// 2. Device cards render (headings come from the seeded device aliases).
	await expect(page.getByRole('heading', { name: 'Living Room Lamp' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Reading Bulb' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Porch Light' })).toBeVisible();

	// 3. Toggling a device updates its card. The plug starts off; clicking its
	//    switch drives a POST /power round-trip and the card reflects the result.
	const lampSwitch = page.getByRole('switch', { name: 'Toggle Living Room Lamp' });
	await expect(lampSwitch).not.toBeChecked();
	await lampSwitch.click();
	await expect(lampSwitch).toBeChecked();

	// 4. A server-side change appears WITHOUT a page reload. The "Porch Light"
	//    fake flips state every time the server re-reads it, which the SSE stream
	//    does on its own interval — so its switch turns on with no interaction and
	//    no navigation here.
	const porchSwitch = page.getByRole('switch', { name: 'Toggle Porch Light' });
	await expect(porchSwitch).not.toBeChecked();
	await expect(porchSwitch).toBeChecked({ timeout: 30_000 });
});
