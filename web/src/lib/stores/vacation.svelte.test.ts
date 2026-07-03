import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';

// Mock the API client so the store is tested in isolation (no network).
vi.mock('$lib/api/client', () => ({
	errorMessage: (e: unknown, fallback = 'Something went wrong') =>
		e instanceof Error ? e.message : fallback,
	getVacation: vi.fn(),
	setVacation: vi.fn()
}));

import * as client from '$lib/api/client';
import { vacationStore } from './vacation.svelte';
import type { VacationStatus } from '$lib/api/types';

const getVacation = client.getVacation as Mock;
const setVacation = client.setVacation as Mock;

function status(over: Partial<VacationStatus> = {}): VacationStatus {
	return {
		enabled: false,
		device_ids: [],
		room_ids: [],
		start_time: null,
		end_time: '23:00',
		min_interval_minutes: 15,
		max_interval_minutes: 45,
		active: false,
		next_switch_ts: null,
		resolved_device_ids: [],
		...over
	};
}

beforeEach(() => {
	vi.clearAllMocks();
	vacationStore.status = status();
	vacationStore.loaded = false;
});

describe('load', () => {
	it('populates status from the server', async () => {
		getVacation.mockResolvedValue(status({ enabled: true, active: true }));
		await vacationStore.load();
		expect(vacationStore.status.enabled).toBe(true);
		expect(vacationStore.active).toBe(true);
		expect(vacationStore.loaded).toBe(true);
	});

	it('degrades to defaults on failure but still marks loaded', async () => {
		getVacation.mockRejectedValue(new Error('offline'));
		await vacationStore.load();
		expect(vacationStore.status.enabled).toBe(false);
		expect(vacationStore.loaded).toBe(true);
	});
});

describe('config', () => {
	it('drops the server-only status fields', () => {
		vacationStore.status = status({ enabled: true, active: true, next_switch_ts: 123 });
		const config = vacationStore.config();
		expect(config).not.toHaveProperty('active');
		expect(config).not.toHaveProperty('next_switch_ts');
		expect(config).not.toHaveProperty('resolved_device_ids');
		expect(config.enabled).toBe(true);
	});
});

describe('save', () => {
	it('trusts the server-echoed status on success', async () => {
		setVacation.mockResolvedValue(status({ enabled: true, active: true, next_switch_ts: 999 }));
		const ok = await vacationStore.save({ ...vacationStore.config(), enabled: true });
		expect(ok).toBe(true);
		expect(vacationStore.status.next_switch_ts).toBe(999);
	});

	it('reverts the optimistic change on failure', async () => {
		vacationStore.status = status({ enabled: false });
		setVacation.mockRejectedValue(new Error('nope'));
		const ok = await vacationStore.save({ ...vacationStore.config(), enabled: true });
		expect(ok).toBe(false);
		expect(vacationStore.status.enabled).toBe(false);
	});
});

describe('active', () => {
	it('is true only when enabled AND server reports active', () => {
		vacationStore.status = status({ enabled: true, active: false });
		expect(vacationStore.active).toBe(false);
		vacationStore.status = status({ enabled: true, active: true });
		expect(vacationStore.active).toBe(true);
	});
});

describe('toggleEnabled', () => {
	it('flips enabled and persists it', async () => {
		vacationStore.status = status({ enabled: false, device_ids: ['d1'] });
		setVacation.mockResolvedValue(status({ enabled: true, device_ids: ['d1'] }));
		await vacationStore.toggleEnabled();
		expect(setVacation).toHaveBeenCalledWith(expect.objectContaining({ enabled: true }));
		expect(vacationStore.status.enabled).toBe(true);
	});
});
