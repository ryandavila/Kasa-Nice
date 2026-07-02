import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';

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
		getRecentAlerts: vi.fn(),
		getAlertThresholds: vi.fn(),
		setAlertThresholds: vi.fn()
	};
});

import * as client from '$lib/api/client';
import { alertStore } from './alerts.svelte';
import type { Alert } from '$lib/api/types';

const getRecentAlerts = client.getRecentAlerts as Mock;
const getAlertThresholds = client.getAlertThresholds as Mock;
const setAlertThresholds = client.setAlertThresholds as Mock;

function alert(over: Partial<Alert> = {}): Alert {
	return {
		id: 'a1',
		ts: 1000,
		type: 'device_unreachable',
		device_id: 'd1',
		message: 'Plug became unreachable',
		power_w: null,
		threshold_w: null,
		...over
	};
}

beforeEach(() => {
	vi.clearAllMocks();
	alertStore.alerts = [];
	alertStore.thresholds = {};
	alertStore.lastSeenId = null;
});

describe('load', () => {
	it('populates alerts from the server', async () => {
		getRecentAlerts.mockResolvedValue([alert()]);
		await alertStore.load();
		expect(alertStore.alerts.map((a) => a.id)).toEqual(['a1']);
	});

	it('keeps the previous list on failure', async () => {
		alertStore.alerts = [alert()];
		getRecentAlerts.mockRejectedValue(new Error('offline'));
		await alertStore.load();
		expect(alertStore.alerts.map((a) => a.id)).toEqual(['a1']);
	});
});

describe('unseen count', () => {
	// Alerts are newest-first, so everything before the last-seen id is unseen.
	it('counts all alerts when nothing has been seen', () => {
		alertStore.alerts = [alert({ id: 'a3' }), alert({ id: 'a2' }), alert({ id: 'a1' })];
		expect(alertStore.unseen).toBe(3);
	});

	it('counts only alerts newer than the last seen id', () => {
		alertStore.alerts = [alert({ id: 'a3' }), alert({ id: 'a2' }), alert({ id: 'a1' })];
		alertStore.lastSeenId = 'a2';
		expect(alertStore.unseen).toBe(1); // only a3 is newer
	});

	it('treats an unknown last-seen id as everything unseen', () => {
		alertStore.alerts = [alert({ id: 'a3' }), alert({ id: 'a2' })];
		alertStore.lastSeenId = 'gone';
		expect(alertStore.unseen).toBe(2);
	});
});

describe('markSeen', () => {
	it('remembers the newest alert id', () => {
		alertStore.alerts = [alert({ id: 'a9' }), alert({ id: 'a1' })];
		alertStore.markSeen();
		expect(alertStore.lastSeenId).toBe('a9');
		expect(alertStore.unseen).toBe(0);
	});

	it('is a no-op with no alerts', () => {
		alertStore.markSeen();
		expect(alertStore.lastSeenId).toBeNull();
	});
});

describe('thresholds', () => {
	it('loads thresholds from the server', async () => {
		getAlertThresholds.mockResolvedValue({ thresholds: { d1: 30 } });
		await alertStore.loadThresholds();
		expect(alertStore.thresholds).toEqual({ d1: 30 });
	});

	it('sets a threshold optimistically and reconciles with the server', async () => {
		setAlertThresholds.mockResolvedValue({ thresholds: { d1: 30 } });
		const p = alertStore.setThreshold('d1', 30);
		// Optimistic update is synchronous, before the request resolves.
		expect(alertStore.thresholds).toEqual({ d1: 30 });
		await p;
		expect(setAlertThresholds).toHaveBeenCalledWith({ d1: 30 });
	});

	it('clearing removes the device from the map', async () => {
		alertStore.thresholds = { d1: 30, d2: 5 };
		setAlertThresholds.mockResolvedValue({ thresholds: { d2: 5 } });
		await alertStore.clearThreshold('d1');
		expect(setAlertThresholds).toHaveBeenCalledWith({ d2: 5 });
		expect(alertStore.thresholds).toEqual({ d2: 5 });
	});

	it('reverts the optimistic change when the PUT fails', async () => {
		alertStore.thresholds = { d1: 10 };
		setAlertThresholds.mockRejectedValue(new Error('offline'));
		await alertStore.setThreshold('d1', 99);
		expect(alertStore.thresholds).toEqual({ d1: 10 });
	});
});
