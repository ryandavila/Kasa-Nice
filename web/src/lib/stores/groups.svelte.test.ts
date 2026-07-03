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
		errorMessage: (e: unknown, fallback = 'Something went wrong') =>
			e instanceof Error ? e.message : fallback,
		listGroups: vi.fn(),
		createGroup: vi.fn(),
		updateGroup: vi.fn(),
		deleteGroup: vi.fn(),
		getFavorites: vi.fn(),
		setFavorites: vi.fn()
	};
});

import * as client from '$lib/api/client';
import { groupStore } from './groups.svelte';

const updateGroup = client.updateGroup as Mock;
const createGroup = client.createGroup as Mock;
const setFavorites = client.setFavorites as Mock;

beforeEach(() => {
	vi.clearAllMocks();
	// Reset the shared singleton to a known state before each test.
	groupStore.groups = [];
	groupStore.favorites = [];
});

describe('favorites', () => {
	it('toggles on optimistically and persists the new list', async () => {
		setFavorites.mockResolvedValue({ device_ids: ['10.0.0.1'] });
		await groupStore.toggleFavorite('10.0.0.1');
		expect(groupStore.isFavorite('10.0.0.1')).toBe(true);
		expect(setFavorites).toHaveBeenCalledWith(['10.0.0.1']);
	});

	it('reverts when persistence fails', async () => {
		groupStore.favorites = ['10.0.0.1'];
		setFavorites.mockRejectedValue(new Error('offline'));
		await groupStore.toggleFavorite('10.0.0.2');
		expect(groupStore.favorites).toEqual(['10.0.0.1']);
	});
});

describe('groupOf', () => {
	it('finds the room a device belongs to, or null', () => {
		groupStore.groups = [
			{ id: 'a', name: 'A', device_ids: ['d1'] },
			{ id: 'b', name: 'B', device_ids: [] }
		];
		expect(groupStore.groupOf('d1')?.id).toBe('a');
		expect(groupStore.groupOf('nope')).toBeNull();
	});
});

describe('assignDevice', () => {
	it('moves a device between rooms and persists both', async () => {
		groupStore.groups = [
			{ id: 'a', name: 'A', device_ids: ['d1'] },
			{ id: 'b', name: 'B', device_ids: [] }
		];
		updateGroup.mockResolvedValue({});

		await groupStore.assignDevice('d1', 'b');

		expect(groupStore.groupOf('d1')?.id).toBe('b');
		expect(updateGroup).toHaveBeenCalledWith('a', { device_ids: [] });
		expect(updateGroup).toHaveBeenCalledWith('b', { device_ids: ['d1'] });
	});

	it('is a no-op when the device is already in the target room', async () => {
		groupStore.groups = [{ id: 'a', name: 'A', device_ids: ['d1'] }];
		await groupStore.assignDevice('d1', 'a');
		expect(updateGroup).not.toHaveBeenCalled();
	});

	it('removes a device from all rooms when target is null', async () => {
		groupStore.groups = [{ id: 'a', name: 'A', device_ids: ['d1'] }];
		updateGroup.mockResolvedValue({});
		await groupStore.assignDevice('d1', null);
		expect(groupStore.groupOf('d1')).toBeNull();
		expect(updateGroup).toHaveBeenCalledWith('a', { device_ids: [] });
	});
});

describe('createRoom', () => {
	it('appends the room returned by the server', async () => {
		createGroup.mockResolvedValue({ id: 'g9', name: 'Garage', device_ids: [] });
		const room = await groupStore.createRoom('Garage');
		expect(room?.id).toBe('g9');
		expect(groupStore.groups.map((g) => g.name)).toContain('Garage');
	});
});
