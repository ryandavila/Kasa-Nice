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
		listScenes: vi.fn(),
		createScene: vi.fn(),
		updateScene: vi.fn(),
		deleteScene: vi.fn(),
		applyScene: vi.fn()
	};
});

import * as client from '$lib/api/client';
import { sceneStore } from './scenes.svelte';
import type { Scene } from '$lib/api/types';

const listScenes = client.listScenes as Mock;
const createScene = client.createScene as Mock;
const updateScene = client.updateScene as Mock;
const deleteScene = client.deleteScene as Mock;
const applyScene = client.applyScene as Mock;

function scene(over: Partial<Scene> = {}): Scene {
	return {
		id: 's1',
		name: 'Movie night',
		entries: [{ device_id: 'd1', state: { on: false } }],
		...over
	};
}

beforeEach(() => {
	vi.clearAllMocks();
	// Reset the shared singleton to a known state before each test.
	sceneStore.scenes = [];
	sceneStore.loaded = false;
	sceneStore.applying = {};
});

describe('load', () => {
	it('populates scenes from the server', async () => {
		listScenes.mockResolvedValue([scene()]);
		await sceneStore.load();
		expect(sceneStore.scenes.map((s) => s.id)).toEqual(['s1']);
		expect(sceneStore.loaded).toBe(true);
	});

	it('degrades to empty on failure but still marks loaded', async () => {
		listScenes.mockRejectedValue(new Error('offline'));
		await sceneStore.load();
		expect(sceneStore.scenes).toEqual([]);
		expect(sceneStore.loaded).toBe(true);
	});
});

describe('createFromDevices', () => {
	it('snapshots the given devices and appends the returned scene', async () => {
		createScene.mockResolvedValue(scene({ id: 's9', name: 'Evening' }));
		const created = await sceneStore.createFromDevices('Evening', ['d1', 'd2']);
		expect(created?.id).toBe('s9');
		expect(createScene).toHaveBeenCalledWith('Evening', { device_ids: ['d1', 'd2'] });
		expect(sceneStore.scenes.map((s) => s.name)).toContain('Evening');
	});

	it('returns null and does not append on failure', async () => {
		createScene.mockRejectedValue(new Error('nope'));
		const created = await sceneStore.createFromDevices('Evening', ['d1']);
		expect(created).toBeNull();
		expect(sceneStore.scenes).toEqual([]);
	});
});

describe('rename', () => {
	it('optimistically renames and persists', async () => {
		sceneStore.scenes = [scene()];
		updateScene.mockResolvedValue(scene({ name: 'Cinema' }));
		await sceneStore.rename('s1', 'Cinema');
		expect(sceneStore.scenes[0].name).toBe('Cinema');
		expect(updateScene).toHaveBeenCalledWith('s1', { name: 'Cinema' });
	});

	it('reverts when persistence fails', async () => {
		sceneStore.scenes = [scene({ name: 'Movie night' })];
		updateScene.mockRejectedValue(new Error('offline'));
		await sceneStore.rename('s1', 'Cinema');
		expect(sceneStore.scenes[0].name).toBe('Movie night');
	});
});

describe('remove', () => {
	it('drops the scene optimistically', async () => {
		sceneStore.scenes = [scene()];
		deleteScene.mockResolvedValue(undefined);
		await sceneStore.remove('s1');
		expect(sceneStore.scenes).toEqual([]);
	});

	it('restores the scene when the delete fails', async () => {
		sceneStore.scenes = [scene()];
		deleteScene.mockRejectedValue(new Error('offline'));
		await sceneStore.remove('s1');
		expect(sceneStore.scenes.map((s) => s.id)).toEqual(['s1']);
	});
});

describe('apply', () => {
	it('clears the busy flag after a successful apply', async () => {
		sceneStore.scenes = [scene()];
		applyScene.mockResolvedValue({ succeeded: ['d1'], failed: [] });
		await sceneStore.apply('s1');
		expect(applyScene).toHaveBeenCalledWith('s1');
		expect(sceneStore.applying['s1']).toBe(false);
	});

	it('does not throw on a partial failure and still clears busy', async () => {
		sceneStore.scenes = [scene()];
		applyScene.mockResolvedValue({ succeeded: [], failed: ['d1'] });
		await sceneStore.apply('s1');
		expect(sceneStore.applying['s1']).toBe(false);
	});

	it('clears busy when the request rejects', async () => {
		sceneStore.scenes = [scene()];
		applyScene.mockRejectedValue(new Error('offline'));
		await sceneStore.apply('s1');
		expect(sceneStore.applying['s1']).toBe(false);
	});
});
