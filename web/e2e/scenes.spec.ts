import { test, expect } from '@playwright/test';

// Scenes e2e: capture the current state of a seeded fake device into a new
// scene, then apply it and watch the device card reflect the captured state.
// Runs against the shared `just e2e` server (see api/testing/fake_devices.py),
// so the scene is uniquely named per run, and every device-state change this
// spec makes is undone at the end — other specs (and smoke.spec.ts) assume
// "Living Room Lamp" starts OFF, so this spec must hand it back exactly as
// found, not just "on" or "off".
test('creates a scene from current device state, applies it, and updates the device card', async ({
	page
}) => {
	const sceneName = `E2E Movie Night ${Date.now()}`;

	await page.goto('/');
	await expect(page.getByRole('heading', { name: 'Living Room Lamp' })).toBeVisible();

	const lampSwitch = page.getByRole('switch', { name: 'Toggle Living Room Lamp' });
	const wasOn = await lampSwitch.isChecked();

	// 1. Turn the lamp ON so the scene captures an "on" snapshot — applying it
	//    later is only observable if applying flips a card that starts off.
	if (!wasOn) {
		await lampSwitch.click();
		await expect(lampSwitch).toBeChecked();
	}

	// 2. Create the scene, capturing the lamp's current (on) state. The empty
	//    state (no scenes yet) shows its own "New scene" button alongside the
	//    toolbar's, so scope to the first (the toolbar's, always present).
	await page.getByRole('button', { name: 'Scenes' }).click();
	await page.getByRole('button', { name: 'New scene' }).first().click();
	await page.getByPlaceholder('e.g. Movie night').fill(sceneName);
	await page.getByRole('button', { name: 'Living Room Lamp' }).click();
	await page.getByRole('button', { name: 'Save scene' }).click();

	// The one ".rounded-card" ancestor of the scene's name is its row: a plain
	// "div", { has } walk would instead match the innermost wrapping div, which
	// doesn't contain the row's Apply/Delete buttons.
	const sceneRow = page.locator('div.rounded-card', { hasText: sceneName });
	await expect(sceneRow).toBeVisible();

	// 3. Turn the lamp off, then apply the scene — it should turn back on.
	await page.getByRole('button', { name: 'Devices' }).click();
	await expect(lampSwitch).toBeChecked();
	await lampSwitch.click();
	await expect(lampSwitch).not.toBeChecked();

	await page.getByRole('button', { name: 'Scenes' }).click();
	await sceneRow.getByRole('button', { name: 'Apply' }).click();

	await page.getByRole('button', { name: 'Devices' }).click();
	await expect(lampSwitch).toBeChecked();

	// 4. Clean up: delete the scene, and restore the lamp to whatever state this
	//    spec found it in, so nothing sharing the server observes a side effect.
	await page.getByRole('button', { name: 'Scenes' }).click();
	await sceneRow.getByRole('button', { name: `Delete ${sceneName}` }).click();
	await expect(page.getByText(sceneName, { exact: true })).toHaveCount(0);

	await page.getByRole('button', { name: 'Devices' }).click();
	if (!wasOn) {
		await lampSwitch.click();
		await expect(lampSwitch).not.toBeChecked();
	}
});
