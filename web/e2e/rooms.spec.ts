import { test, expect } from '@playwright/test';

// Rooms e2e: create a room, assign a seeded fake device to it, use the room's
// all-off toggle, and see the device card update. Runs against the shared
// `just e2e` server, so the room is uniquely named per run and deleted at the
// end (deleting a room only unassigns its devices, never deletes them) to keep
// this spec independent of others sharing that server.
test('assigns a device to a room and toggles the whole room off', async ({ page }) => {
	const roomName = `E2E Study ${Date.now()}`;

	await page.goto('/');

	// 1. Make sure the target device is ON, so the room's "off" toggle below has
	//    an observable effect.
	const lampSwitch = page.getByRole('switch', { name: 'Toggle Living Room Lamp' });
	if (!(await lampSwitch.isChecked())) {
		await lampSwitch.click();
		await expect(lampSwitch).toBeChecked();
	}

	// 2. Assign the device to a new room from the "By room" grouping, where each
	//    card exposes a room picker.
	await page.getByRole('button', { name: 'By room' }).click();
	await page.getByRole('button', { name: 'New room' }).click();
	await page.getByPlaceholder('Room name').fill(roomName);
	// Exact match: a substring match on "Add" would also hit every card's "Add
	// to favorites" button.
	await page.getByRole('button', { name: 'Add', exact: true }).click();
	await expect(page.getByRole('heading', { name: roomName })).toBeVisible();

	// The card lives under "No room"/"All devices" until assigned; its room
	// picker is a <select> labelled for that specific device.
	await page.getByLabel('Room for Living Room Lamp').selectOption({ label: roomName });

	const roomSection = page.locator('section', {
		has: page.getByRole('heading', { name: roomName })
	});
	await expect(roomSection.getByRole('heading', { name: 'Living Room Lamp' })).toBeVisible();

	// 3. Room all-off: the room header's toggle switches every device in it.
	const roomToggle = roomSection.getByRole('switch', { name: `Toggle all in ${roomName}` });
	await expect(roomToggle).toBeChecked();
	await roomToggle.click();
	await expect(roomToggle).not.toBeChecked();
	await expect(lampSwitch).not.toBeChecked();

	// 4. Clean up: delete the room (unassigns the device, doesn't delete it) so
	//    this spec leaves no state behind for others sharing the server process.
	await roomSection.getByRole('button', { name: `Delete ${roomName}` }).click();
	await expect(page.getByRole('heading', { name: roomName })).toHaveCount(0);
});
