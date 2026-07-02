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
		listSchedules: vi.fn(),
		createSchedule: vi.fn(),
		updateSchedule: vi.fn(),
		deleteSchedule: vi.fn()
	};
});

import * as client from '$lib/api/client';
import { scheduleStore } from './schedules.svelte';
import type { Schedule } from '$lib/api/types';

const listSchedules = client.listSchedules as Mock;
const createSchedule = client.createSchedule as Mock;
const updateSchedule = client.updateSchedule as Mock;
const deleteSchedule = client.deleteSchedule as Mock;

function rule(over: Partial<Schedule> = {}): Schedule {
	return {
		id: 'r1',
		kind: 'fixed_time',
		enabled: true,
		time: '18:30',
		days: [0, 1, 2],
		offset_minutes: 0,
		at: null,
		target: { type: 'device', id: 'd1' },
		action: 'on',
		scene_id: null,
		last_fired: null,
		...over
	};
}

beforeEach(() => {
	vi.clearAllMocks();
	scheduleStore.rules = [];
	scheduleStore.loaded = false;
});

describe('load', () => {
	it('populates rules from the server', async () => {
		listSchedules.mockResolvedValue([rule()]);
		await scheduleStore.load();
		expect(scheduleStore.rules.map((r) => r.id)).toEqual(['r1']);
		expect(scheduleStore.loaded).toBe(true);
	});

	it('degrades to empty on failure but still marks loaded', async () => {
		listSchedules.mockRejectedValue(new Error('offline'));
		await scheduleStore.load();
		expect(scheduleStore.rules).toEqual([]);
		expect(scheduleStore.loaded).toBe(true);
	});
});

describe('create', () => {
	it('appends the rule returned by the server', async () => {
		createSchedule.mockResolvedValue(rule({ id: 'r9' }));
		const created = await scheduleStore.create({
			time: '07:00',
			days: [1],
			target: { type: 'device', id: 'd1' },
			action: 'on'
		});
		expect(created?.id).toBe('r9');
		expect(scheduleStore.rules.map((r) => r.id)).toContain('r9');
	});

	it('returns null and does not append on failure', async () => {
		createSchedule.mockRejectedValue(new Error('nope'));
		const created = await scheduleStore.create({
			time: '07:00',
			days: [1],
			target: { type: 'device', id: 'd1' },
			action: 'on'
		});
		expect(created).toBeNull();
		expect(scheduleStore.rules).toEqual([]);
	});

	it('passes new-kind and scene-action fields through to the server', async () => {
		createSchedule.mockResolvedValue(
			rule({ id: 'r7', kind: 'sunset', time: null, offset_minutes: -15 })
		);
		await scheduleStore.create({
			kind: 'sunset',
			days: [0, 1],
			offset_minutes: -15,
			target: { type: 'device', id: 'd1' },
			action: 'on'
		});
		expect(createSchedule).toHaveBeenCalledWith(
			expect.objectContaining({ kind: 'sunset', offset_minutes: -15 })
		);
		expect(scheduleStore.rules.map((r) => r.id)).toContain('r7');
	});

	it('creates a one-shot scene rule (at + scene_id, no target)', async () => {
		createSchedule.mockResolvedValue(
			rule({
				id: 'r8',
				kind: 'once',
				time: null,
				at: '2024-06-01T07:15',
				action: 'scene',
				scene_id: 's1'
			})
		);
		const created = await scheduleStore.create({
			kind: 'once',
			at: '2024-06-01T07:15',
			action: 'scene',
			scene_id: 's1'
		});
		expect(created?.scene_id).toBe('s1');
		expect(createSchedule).toHaveBeenCalledWith(
			expect.objectContaining({ kind: 'once', at: '2024-06-01T07:15', scene_id: 's1' })
		);
	});
});

describe('toggleEnabled', () => {
	it('optimistically flips enabled and persists it', async () => {
		scheduleStore.rules = [rule({ enabled: true })];
		updateSchedule.mockResolvedValue(rule({ enabled: false }));
		scheduleStore.toggleEnabled('r1');
		// Optimistic flip is synchronous, before the request resolves.
		expect(scheduleStore.rules[0].enabled).toBe(false);
		expect(updateSchedule).toHaveBeenCalledWith('r1', { enabled: false });
	});

	it('reverts when persistence fails', async () => {
		scheduleStore.rules = [rule({ enabled: true })];
		updateSchedule.mockRejectedValue(new Error('offline'));
		await scheduleStore.update('r1', { enabled: false });
		expect(scheduleStore.rules[0].enabled).toBe(true);
	});
});

describe('update', () => {
	it('replaces the rule with the server copy on success', async () => {
		scheduleStore.rules = [rule()];
		updateSchedule.mockResolvedValue(rule({ time: '09:00', days: [3] }));
		await scheduleStore.update('r1', { time: '09:00', days: [3] });
		expect(scheduleStore.rules[0].time).toBe('09:00');
		expect(scheduleStore.rules[0].days).toEqual([3]);
	});
});

describe('remove', () => {
	it('drops the rule optimistically', async () => {
		scheduleStore.rules = [rule()];
		deleteSchedule.mockResolvedValue(undefined);
		await scheduleStore.remove('r1');
		expect(scheduleStore.rules).toEqual([]);
	});

	it('restores the rule when the delete fails', async () => {
		scheduleStore.rules = [rule()];
		deleteSchedule.mockRejectedValue(new Error('offline'));
		await scheduleStore.remove('r1');
		expect(scheduleStore.rules.map((r) => r.id)).toEqual(['r1']);
	});
});
