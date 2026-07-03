import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
	ApiError,
	listDevices,
	setPower,
	getHistory,
	createGroup,
	deleteGroup,
	setFavorites,
	setGroupPower,
	setAllPower,
	getEnergySummary,
	getBackup,
	restoreBackup
} from './client';

function mockFetch(impl: (url: string, init?: RequestInit) => unknown) {
	const fn = vi.fn(async (url: string, init?: RequestInit) => {
		const result = impl(url, init);
		return result as Response;
	});
	globalThis.fetch = fn as unknown as typeof fetch;
	return fn;
}

function ok(body: unknown, status = 200): Response {
	return {
		ok: true,
		status,
		json: async () => body
	} as Response;
}

beforeEach(() => {
	vi.restoreAllMocks();
});

afterEach(() => {
	vi.restoreAllMocks();
});

describe('request', () => {
	it('GETs a path under /api and returns the parsed body', async () => {
		const fetchFn = mockFetch(() => ok([{ id: '10.0.0.1' }]));
		const devices = await listDevices();
		expect(devices).toEqual([{ id: '10.0.0.1' }]);
		expect(fetchFn).toHaveBeenCalledWith('/api/devices', expect.anything());
	});

	it('throws ApiError carrying status and the server detail', async () => {
		mockFetch(() => ({
			ok: false,
			status: 404,
			statusText: 'Not Found',
			json: async () => ({ detail: 'Unknown device: x' })
		}));
		await expect(listDevices()).rejects.toMatchObject({
			name: 'ApiError',
			status: 404,
			message: 'Unknown device: x'
		});
		await expect(listDevices()).rejects.toBeInstanceOf(ApiError);
	});

	it('falls back to status text when the error body is not JSON', async () => {
		mockFetch(() => ({
			ok: false,
			status: 500,
			statusText: 'Internal Server Error',
			json: async () => {
				throw new Error('not json');
			}
		}));
		await expect(listDevices()).rejects.toMatchObject({
			status: 500,
			message: 'Internal Server Error'
		});
	});

	it('returns undefined for a 204 No Content (DELETE)', async () => {
		mockFetch(() => ({ ok: true, status: 204 }) as Response);
		await expect(deleteGroup('abc')).resolves.toBeUndefined();
	});
});

describe('endpoint shapes', () => {
	it('setPower POSTs the on flag to the device path', async () => {
		const fetchFn = mockFetch(() => ok({ id: '10.0.0.1', is_on: true }));
		await setPower('10.0.0.1', true);
		const [url, init] = fetchFn.mock.calls[0];
		expect(url).toBe('/api/devices/10.0.0.1/power');
		expect(init).toMatchObject({ method: 'POST', body: JSON.stringify({ on: true }) });
	});

	it('getHistory encodes the id and passes the window query params', async () => {
		const fetchFn = mockFetch(() => ok({ device_id: 'x', samples: [], daily: [] }));
		await getHistory('10.0.0.4', 12, 7);
		expect(fetchFn.mock.calls[0][0]).toBe('/api/devices/10.0.0.4/history?hours=12&days=7');
	});

	it('createGroup POSTs the name', async () => {
		const fetchFn = mockFetch(() => ok({ id: 'g1', name: 'Den', device_ids: [] }));
		await createGroup('Den');
		const [url, init] = fetchFn.mock.calls[0];
		expect(url).toBe('/api/groups');
		expect(init).toMatchObject({ method: 'POST', body: JSON.stringify({ name: 'Den' }) });
	});

	it('setFavorites PUTs the id list', async () => {
		const fetchFn = mockFetch(() => ok({ device_ids: ['10.0.0.1'] }));
		await setFavorites(['10.0.0.1']);
		const [url, init] = fetchFn.mock.calls[0];
		expect(url).toBe('/api/favorites');
		expect(init).toMatchObject({
			method: 'PUT',
			body: JSON.stringify({ device_ids: ['10.0.0.1'] })
		});
	});

	it('setGroupPower POSTs the on flag to the room power path', async () => {
		const fetchFn = mockFetch(() => ok({ on: false, succeeded: [], failed: [] }));
		await setGroupPower('room 1', false);
		const [url, init] = fetchFn.mock.calls[0];
		expect(url).toBe('/api/groups/room%201/power');
		expect(init).toMatchObject({ method: 'POST', body: JSON.stringify({ on: false }) });
	});

	it('getEnergySummary GETs the whole-home summary path', async () => {
		const body = {
			total_power_w: 25,
			today_kwh: 0.6,
			month_kwh: 8.4,
			today_cost: null,
			month_cost: null,
			device_count: 2
		};
		const fetchFn = mockFetch(() => ok(body));
		const result = await getEnergySummary();
		expect(fetchFn.mock.calls[0][0]).toBe('/api/energy/summary');
		expect(result).toEqual(body);
	});

	it('setAllPower POSTs the on flag to the global power path', async () => {
		const fetchFn = mockFetch(() => ok({ on: false, succeeded: ['a'], failed: [] }));
		const result = await setAllPower(false);
		const [url, init] = fetchFn.mock.calls[0];
		expect(url).toBe('/api/power');
		expect(init).toMatchObject({ method: 'POST', body: JSON.stringify({ on: false }) });
		expect(result).toEqual({ on: false, succeeded: ['a'], failed: [] });
	});

	it('getBackup GETs the backup document path', async () => {
		const body = {
			backup_version: 1,
			created_at: '2026-01-01T00:00:00Z',
			app_version: '1.1.0',
			groups: [],
			favorites: [],
			scenes: [],
			schedules: [],
			alert_thresholds: {},
			known_devices: []
		};
		const fetchFn = mockFetch(() => ok(body));
		const result = await getBackup();
		expect(fetchFn.mock.calls[0][0]).toBe('/api/backup');
		expect(result).toEqual(body);
	});

	it('restoreBackup POSTs the document to the restore path', async () => {
		const doc = {
			backup_version: 1,
			created_at: '2026-01-01T00:00:00Z',
			app_version: '1.1.0',
			groups: [],
			favorites: [],
			scenes: [],
			schedules: [],
			alert_thresholds: {},
			known_devices: []
		};
		const fetchFn = mockFetch(() => ok(doc));
		await restoreBackup(doc);
		const [url, init] = fetchFn.mock.calls[0];
		expect(url).toBe('/api/backup/restore');
		expect(init).toMatchObject({ method: 'POST', body: JSON.stringify(doc) });
	});
});
