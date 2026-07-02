import {
	listScenes,
	createScene,
	updateScene,
	deleteScene,
	applyScene,
	ApiError
} from '$lib/api/client';
import type { Scene } from '$lib/api/types';
import { toasts } from './toasts.svelte';

function message(e: unknown): string {
	if (e instanceof ApiError) return e.message;
	return e instanceof Error ? e.message : 'Something went wrong';
}

/**
 * Named scenes — a saved per-device state applied as one action. The backend
 * owns applying (a partial-failure-tolerant fan-out); this store is the editor
 * over the scene list plus the apply trigger. Edits are optimistic — applied
 * locally at once, persisted in the background, reverted with a toast on failure
 * — mirroring the groups and schedules stores.
 */
class ScenesStore {
	scenes = $state<Scene[]>([]);
	loaded = $state(false);
	/** Scene ids with an apply in flight, for per-scene busy state. */
	applying = $state<Record<string, boolean>>({});

	async load() {
		try {
			this.scenes = await listScenes();
		} catch {
			// Scenes are best-effort in the UI; a load failure just shows empty.
		} finally {
			this.loaded = true;
		}
	}

	/** Capture the current state of the given devices as a new scene. */
	async createFromDevices(name: string, deviceIds: string[]): Promise<Scene | null> {
		try {
			const scene = await createScene(name, { device_ids: deviceIds });
			this.scenes = [...this.scenes, scene];
			return scene;
		} catch (e) {
			toasts.push(message(e), 'error');
			return null;
		}
	}

	async rename(id: string, name: string) {
		const prev = this.scenes;
		this.scenes = this.scenes.map((s) => (s.id === id ? { ...s, name } : s));
		try {
			await updateScene(id, { name });
		} catch (e) {
			this.scenes = prev;
			toasts.push(message(e), 'error');
		}
	}

	async remove(id: string) {
		const prev = this.scenes;
		this.scenes = this.scenes.filter((s) => s.id !== id);
		try {
			await deleteScene(id);
		} catch (e) {
			this.scenes = prev;
			toasts.push(message(e), 'error');
		}
	}

	/**
	 * Apply a scene, surfacing the server's per-device result: a clean success, a
	 * partial-failure count, or a hard error. Marks the scene busy so the button
	 * can show progress and can't be double-fired.
	 */
	async apply(id: string) {
		this.applying[id] = true;
		const name = this.scenes.find((s) => s.id === id)?.name ?? 'Scene';
		try {
			const { failed } = await applyScene(id);
			if (failed.length) {
				toasts.push(
					`${name}: ${failed.length} device${failed.length === 1 ? '' : 's'} didn't respond`,
					'error'
				);
			} else {
				toasts.push(`${name} applied`, 'on');
			}
		} catch (e) {
			toasts.push(message(e), 'error');
		} finally {
			this.applying[id] = false;
		}
	}
}

export const sceneStore = new ScenesStore();
