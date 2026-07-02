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
const setBrightness = client.setBrightness as Mock;
const setColorHex = client.setColorHex as Mock;
const setPower = client.setPower as Mock;

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

function makeDevice(over: Partial<Device> = {}): Device {
	return {
		id: '10.0.0.1',
		alias: 'Lamp',
		host: '10.0.0.1',
		model: 'KL130',
		device_type: 'Bulb',
		is_on: false,
		is_color: true,
		is_dimmable: true,
		has_emeter: false,
		brightness: 40,
		hsv: [120, 100, 100],
		children: [],
		...over
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

describe('setBrightness optimistic revert', () => {
	it('applies the new value optimistically then persists', async () => {
		const d = makeDevice({ brightness: 40, is_on: false });
		deviceStore.devices = [d];
		setBrightness.mockResolvedValue(makeDevice({ brightness: 80, is_on: true }));
		await deviceStore.setBrightness(d, 80);
		expect(setBrightness).toHaveBeenCalledWith('10.0.0.1', 80);
		expect(deviceStore.devices[0].brightness).toBe(80);
		expect(deviceStore.devices[0].is_on).toBe(true);
	});

	it('reverts brightness and power when the request fails', async () => {
		const d = makeDevice({ brightness: 40, is_on: false });
		deviceStore.devices = [d];
		setBrightness.mockRejectedValue(new Error('offline'));
		await deviceStore.setBrightness(d, 80);
		expect(d.brightness).toBe(40);
		expect(d.is_on).toBe(false);
	});
});

describe('setColor optimistic revert', () => {
	it('reverts hsv and power when the request fails', async () => {
		const d = makeDevice({ hsv: [200, 50, 50], is_on: false });
		deviceStore.devices = [d];
		setColorHex.mockRejectedValue(new Error('offline'));
		await deviceStore.setColor(d, '#ff0000');
		expect(d.hsv).toEqual([200, 50, 50]);
		expect(d.is_on).toBe(false);
	});
});

describe('merge with sparse SSE frames', () => {
	it('is idempotent: applying the same frame twice leaves state unchanged', () => {
		const d = makeDevice({ is_on: true, brightness: 60 });
		deviceStore.devices = [d];
		const frame = [makeDevice({ is_on: true, brightness: 60 })];
		// @ts-expect-error - exercise the private merge directly
		deviceStore.merge(frame);
		// @ts-expect-error - exercise the private merge directly
		deviceStore.merge(frame);
		expect(deviceStore.devices[0].is_on).toBe(true);
		expect(deviceStore.devices[0].brightness).toBe(60);
	});

	it('skips devices with an in-flight request so a frame never clobbers optimism', async () => {
		const d = makeDevice({ is_on: false });
		deviceStore.devices = [d];
		// Hold the request open so the device is "busy" while a frame arrives.
		let resolve!: (v: Device) => void;
		setPower.mockReturnValue(new Promise<Device>((r) => (resolve = r)));
		const pending = deviceStore.togglePower(d, true);
		// A stale frame reports the device still off; merge must ignore it.
		// @ts-expect-error - exercise the private merge directly
		deviceStore.merge([makeDevice({ is_on: false })]);
		expect(d.is_on).toBe(true); // optimistic value survives
		resolve(makeDevice({ is_on: true }));
		await pending;
	});
});
