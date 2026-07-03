import { test, expect } from '@playwright/test';

// End-to-end smoke test: drives a real browser against the production build the
// API serves (KASA_FAKE_DEVICES=1), exercising SPA -> REST -> registry and the
// live SSE stream. Seeded fakes (see api/testing/fake_devices.py): "Living Room
// Lamp" (plain plug, toggled by hand), "Reading Bulb" (colour bulb), and "Porch
// Light" (flips its own state on every read, driving the live-update assertion).
//
// This spec runs alongside scenes/schedules/alerts/rooms.spec.ts against the
// same shared server (see the `e2e` justfile recipe); those toggle Living Room
// Lamp but always restore it, so it's OFF here regardless of run order. Porch
// Light is different: the alert evaluator and energy recorder also run in fake
// mode now (they need real cycles for the alerts spec) and refresh every fake
// device — including Porch Light — on their own cadence, so by the time this
// spec loads, its on/update-triggered flips may have happened an odd or even
// number of times. Read its actual starting state rather than assuming OFF, so
// this spec only asserts what it actually controls: that the switch flips
// exactly once more within the timeout, proving the SSE stream delivers a
// server-initiated change with no interaction and no navigation.
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

	// 3. Toggling a device updates its card. Other specs sharing this server
	//    always leave the lamp OFF when they finish, so it's OFF here regardless
	//    of which specs ran before this one. Clicking its switch drives a
	//    POST /power round-trip and the card reflects the result.
	const lampSwitch = page.getByRole('switch', { name: 'Toggle Living Room Lamp' });
	await expect(lampSwitch).not.toBeChecked();
	await lampSwitch.click();
	await expect(lampSwitch).toBeChecked();

	// 4. A server-side change appears WITHOUT a page reload. The "Porch Light"
	//    fake flips state every time the server re-reads it, which both the SSE
	//    stream and the alert evaluator's background refresh do on their own
	//    intervals — so its switch flips with no interaction and no navigation
	//    here. Read the actual starting value instead of assuming OFF.
	const porchSwitch = page.getByRole('switch', { name: 'Toggle Porch Light' });
	const porchStartedOn = await porchSwitch.isChecked();
	if (porchStartedOn) {
		await expect(porchSwitch).not.toBeChecked({ timeout: 30_000 });
	} else {
		await expect(porchSwitch).toBeChecked({ timeout: 30_000 });
	}
});
