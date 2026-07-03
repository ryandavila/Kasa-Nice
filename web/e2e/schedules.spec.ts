import { test, expect } from '@playwright/test';

// Schedules e2e: create a fixed-time rule targeting a seeded fake device, edit
// its time, disable it, then delete it. Runs against the shared `just e2e`
// server, so every assertion keys off the rule's own row (found by its
// distinctive time) rather than "the only rule", keeping this independent of
// whatever other specs leave behind on that shared server.
test('creates a fixed-time schedule, edits it, disables it, and deletes it', async ({ page }) => {
	await page.goto('/');
	await page.getByRole('button', { name: 'Schedules' }).click();

	// 1. Create: fixed time 03:17, turning on "Reading Bulb", every day. An
	//    unusual time (not a "round" default) so the row is unambiguous to find.
	//    The empty state (no schedules yet) shows its own "New schedule" button
	//    alongside the toolbar's, so scope to the first (the toolbar's, always
	//    present).
	await page.getByRole('button', { name: 'New schedule' }).first().click();
	await page.getByLabel('Time').fill('03:17');
	await page.getByLabel('Target').selectOption({ label: 'Reading Bulb' });
	await page.getByRole('button', { name: 'Every day' }).click();
	await page.getByRole('button', { name: 'Create' }).click();

	// Each rule row is the one ".rounded-card" ancestor of its trigger headline
	// (a plain "div", { has }) walk would instead match the innermost wrapping
	// div, which doesn't contain the row's other controls/labels).
	const row = page.locator('div.rounded-card', { hasText: '03:17' });
	await expect(row).toBeVisible();
	await expect(row.getByText('Reading Bulb')).toBeVisible();

	// 2. Edit: change the time to 04:22. The composer re-opens pre-filled, so
	//    only the time field needs changing before saving.
	await row.getByRole('button', { name: 'Edit schedule' }).click();
	await page.getByLabel('Time').fill('04:22');
	await page.getByRole('button', { name: 'Save' }).click();

	const editedRow = page.locator('div.rounded-card', { hasText: '04:22' });
	await expect(editedRow).toBeVisible();
	await expect(page.getByText('03:17', { exact: true })).toHaveCount(0);

	// 3. Disable: the toggle flips off and the row dims (opacity class), without
	//    deleting the rule.
	const enableToggle = editedRow.getByRole('switch', { name: 'Enable schedule' });
	await expect(enableToggle).toBeChecked();
	await enableToggle.click();
	await expect(enableToggle).not.toBeChecked();

	// 4. Delete: the row disappears entirely.
	await editedRow.getByRole('button', { name: 'Delete schedule' }).click();
	await expect(page.getByText('04:22', { exact: true })).toHaveCount(0);
});
