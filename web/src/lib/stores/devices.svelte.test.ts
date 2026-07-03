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
		setChildPower: vi.fn(),
		setGroupPower: vi.fn(),
		setAllPower: vi.fn(),
		renameDevice: vi.fn(),
		renameChild: vi.fn()
	};
});

// Capture toast messages so we can assert the child toggle labels by alias.
const pushed: { message: string; kind: string }[] = [];
vi.mock('./toasts.svelte', () => ({
	toasts: { push: (message: string, kind: string) => pushed.push({ message, kind }) }
}));

import * as client from '$lib/api/client';
import { deviceStore } from './devices.svelte';

const discoverDevices = client.discoverDevices as Mock;
const setChildPower = client.setChildPower as Mock;
const setBrightness = client.setBrightness as Mock;
const setColorHex = client.setColorHex as Mock;
const setPower = client.setPower as Mock;
const setGroupPower = client.setGroupPower as Mock;
const setAllPower = client.setAllPower as Mock;
const renameDevice = client.renameDevice as Mock;
const renameChild = client.renameChild as Mock;

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
		reachable: true,
		children: [{ id: 'STRIP_00', alias: 'Soldering Iron', is_on: false }],
		can_rename: true
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
		reachable: true,
		children: [],
		can_rename: true,
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

describe('setGroupPower / setAllPower fan-out', () => {
	it('optimistically switches every device and keeps them on full success', async () => {
		const a = makeDevice({ id: 'a', is_on: false });
		const b = makeDevice({ id: 'b', is_on: false });
		deviceStore.devices = [a, b];
		setGroupPower.mockResolvedValue({ on: true, succeeded: ['a', 'b'], failed: [] });

		await deviceStore.setGroupPower('room1', [a, b], true);

		expect(setGroupPower).toHaveBeenCalledWith('room1', true);
		expect(a.is_on).toBe(true);
		expect(b.is_on).toBe(true);
		expect(pushed).toHaveLength(0); // no error toast on full success
	});

	it('rolls back only the failed devices and toasts the count on partial failure', async () => {
		const a = makeDevice({ id: 'a', is_on: false });
		const b = makeDevice({ id: 'b', is_on: false });
		deviceStore.devices = [a, b];
		setGroupPower.mockResolvedValue({ on: true, succeeded: ['a'], failed: ['b'] });

		await deviceStore.setGroupPower('room1', [a, b], true);

		expect(a.is_on).toBe(true); // succeeded: stays switched
		expect(b.is_on).toBe(false); // failed: reverted
		expect(pushed[0].kind).toBe('error');
		expect(pushed[0].message).toBe("1 device didn't respond");
	});

	it('reverts every device when the whole request fails', async () => {
		const a = makeDevice({ id: 'a', is_on: true });
		const b = makeDevice({ id: 'b', is_on: true });
		deviceStore.devices = [a, b];
		setAllPower.mockRejectedValue(new Error('offline'));

		await deviceStore.setAllPower([a, b], false);

		expect(setAllPower).toHaveBeenCalledWith(false);
		expect(a.is_on).toBe(true); // reverted
		expect(b.is_on).toBe(true);
		expect(pushed[0].kind).toBe('error');
	});
});

describe('renameDevice optimistic revert', () => {
	it('applies the new alias optimistically then persists the server value', async () => {
		const d = makeDevice({ alias: 'Lamp' });
		deviceStore.devices = [d];
		renameDevice.mockResolvedValue(makeDevice({ alias: 'Desk Lamp' }));
		await deviceStore.renameDevice(d, 'Desk Lamp');
		expect(renameDevice).toHaveBeenCalledWith('10.0.0.1', 'Desk Lamp');
		expect(deviceStore.devices[0].alias).toBe('Desk Lamp');
	});

	it('reverts the alias when the request fails', async () => {
		const d = makeDevice({ alias: 'Lamp' });
		deviceStore.devices = [d];
		renameDevice.mockRejectedValue(new Error('offline'));
		await deviceStore.renameDevice(d, 'Desk Lamp');
		expect(d.alias).toBe('Lamp'); // reverted
		expect(pushed[0].kind).toBe('error');
	});
});

describe('retryDevice', () => {
	it('replaces an unreachable placeholder with the live device it comes back as', async () => {
		// A never-read host is keyed by its host; its live identity keys by MAC, so
		// the two would coexist unless the stale placeholder is dropped on recovery.
		const placeholder = makeDevice({
			id: '10.0.0.5',
			host: '10.0.0.5',
			alias: '10.0.0.5',
			reachable: false
		});
		deviceStore.devices = [placeholder];
		const live = makeDevice({ id: 'AABBCC', host: '10.0.0.5', alias: 'Lamp', reachable: true });
		discoverDevices.mockResolvedValue([live]);

		await deviceStore.retryDevice(placeholder);

		expect(discoverDevices).toHaveBeenCalledWith('10.0.0.5');
		expect(deviceStore.devices).toHaveLength(1); // placeholder gone, not duplicated
		expect(deviceStore.devices[0].id).toBe('AABBCC');
		expect(deviceStore.devices[0].reachable).toBe(true);
	});

	it('keeps the card and toasts when the device is still unreachable', async () => {
		const placeholder = makeDevice({ id: '10.0.0.5', host: '10.0.0.5', reachable: false });
		deviceStore.devices = [placeholder];
		discoverDevices.mockResolvedValue([]); // nothing answered

		await deviceStore.retryDevice(placeholder);

		expect(deviceStore.devices).toHaveLength(1); // still shown
		expect(deviceStore.devices[0].reachable).toBe(false);
		expect(pushed[0].kind).toBe('error');
	});
});

describe('renameChild optimistic revert', () => {
	it('sends the parent + stable outlet id and renames optimistically', async () => {
		const device = strip();
		renameChild.mockResolvedValue(device);
		await deviceStore.renameChild(device, 'STRIP_00', 'Lamp');
		expect(renameChild).toHaveBeenCalledWith('STRIPMAC', 'STRIP_00', 'Lamp');
		expect(device.children[0].alias).toBe('Lamp');
	});

	it('reverts the outlet alias when the request fails', async () => {
		const device = strip();
		renameChild.mockRejectedValue(new Error('offline'));
		await deviceStore.renameChild(device, 'STRIP_00', 'Lamp');
		expect(device.children[0].alias).toBe('Soldering Iron'); // reverted
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

	it('applies a rename pushed from another client (device and outlet alias)', () => {
		// Regression: merge only copied power/light state, so a rename pushed via
		// the SSE stream never reached other open tabs until a full reload.
		const d = strip();
		deviceStore.devices = [d];
		const renamed = strip();
		renamed.alias = 'Desk Strip';
		renamed.children[0].alias = 'Hot Air Station';
		// @ts-expect-error - exercise the private merge directly
		deviceStore.merge([renamed]);
		expect(deviceStore.devices[0].alias).toBe('Desk Strip');
		expect(deviceStore.devices[0].children[0].alias).toBe('Hot Air Station');
	});

	it('restores capability flags when a blanked snapshot placeholder recovers', () => {
		// Regression: a device first served as an unreachable snapshot has its
		// capabilities blanked server-side; on recovery merge must restore them or
		// a color bulb stays a bare on/off card until reload.
		const placeholder = makeDevice({
			reachable: false,
			is_on: false,
			is_color: false,
			is_dimmable: false,
			has_emeter: false,
			brightness: null,
			hsv: null
		});
		deviceStore.devices = [placeholder];
		// @ts-expect-error - exercise the private merge directly
		deviceStore.merge([makeDevice({ is_on: true })]); // the live device returns
		const cur = deviceStore.devices[0];
		expect(cur.reachable).toBe(true);
		expect(cur.is_dimmable).toBe(true);
		expect(cur.is_color).toBe(true);
		expect(cur.brightness).toBe(40);
	});

	it('adopts children a childless placeholder gains on recovery', () => {
		const placeholder = { ...strip(), reachable: false, children: [] };
		deviceStore.devices = [placeholder];
		// @ts-expect-error - exercise the private merge directly
		deviceStore.merge([strip()]);
		expect(deviceStore.devices[0].children.map((c) => c.id)).toEqual(['STRIP_00']);
	});
});
