import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import type { Device } from '$lib/api/types';

// Mock the API client so the store is tested in isolation (no network).
vi.mock('$lib/api/client', () => {
	class ApiError extends Error {
		constructor(
			public status: number,
			message: string
		) {
			super(message);
		}
	}
	return {
		ApiError,
		listDevices: vi.fn(),
		getState: vi.fn(),
		getStatus: vi.fn(),
		discoverDevices: vi.fn(),
		scanSubnet: vi.fn(),
		setPower: vi.fn(),
		setBrightness: vi.fn(),
		setColorHex: vi.fn(),
		setChildPower: vi.fn()
	};
});

// Capture toast messages so we can assert the child toggle labels by alias.
const pushed: { message: string; kind: string }[] = [];
vi.mock('./toasts.svelte', () => ({
	toasts: { push: (message: string, kind: string) => pushed.push({ message, kind }) }
}));

import * as client from '$lib/api/client';
import { deviceStore } from './devices.svelte';

const setChildPower = client.setChildPower as Mock;

function strip(): Device {
	return {
		id: 'STRIPMAC',
		alias: 'Bench Strip',
		host: '10.0.0.5',
		model: 'HS300',
		device_type: 'Strip',
		is_on: true,
		is_color: false,
		is_dimmable: false,
		has_emeter: true,
		brightness: null,
		hsv: null,
		children: [{ id: 'STRIP_00', alias: 'Soldering Iron', is_on: false }]
	};
}

beforeEach(() => {
	vi.clearAllMocks();
	pushed.length = 0;
	deviceStore.devices = [];
	deviceStore.busy = {};
});

describe('toggleChild', () => {
	it('sends the stable child id and toasts the alias, not the id', async () => {
		const device = strip();
		setChildPower.mockResolvedValue(device);

		await deviceStore.toggleChild(device, 'STRIP_00', true);

		// The request targets the parent id + the stable outlet id.
		expect(setChildPower).toHaveBeenCalledWith('STRIPMAC', 'STRIP_00', true);
		// The toast reads the human alias, not the opaque stable id.
		expect(pushed[0].message).toBe('Soldering Iron on');
	});

	it('optimistically toggles the outlet and reverts on failure', async () => {
		const device = strip();
		setChildPower.mockRejectedValue(new Error('offline'));

		await deviceStore.toggleChild(device, 'STRIP_00', true);

		expect(device.children[0].is_on).toBe(false); // reverted
	});
});
