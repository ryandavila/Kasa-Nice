import {
	listGroups,
	createGroup,
	updateGroup,
	deleteGroup,
	getFavorites,
	setFavorites,
	ApiError
} from '$lib/api/client';
import type { Group } from '$lib/api/types';
import { toasts } from './toasts.svelte';

function message(e: unknown): string {
	if (e instanceof ApiError) return e.message;
	return e instanceof Error ? e.message : 'Something went wrong';
}

/**
 * Rooms (groups) and favorites — a purely organizational layer over the flat
 * device list. State is optimistic: local edits apply immediately and are
 * persisted in the background, reverting on failure. Decoupled from discovery,
 * so a room may reference a device that is momentarily offline.
 */
class GroupsStore {
	groups = $state<Group[]>([]);
	favorites = $state<string[]>([]);
	loaded = $state(false);

	async load() {
		try {
			const [groups, favorites] = await Promise.all([listGroups(), getFavorites()]);
			this.groups = groups;
			this.favorites = favorites.device_ids;
		} catch {
			// Rooms are best-effort; the UI falls back to grouping by type.
		} finally {
			this.loaded = true;
		}
	}

	isFavorite(deviceId: string) {
		return this.favorites.includes(deviceId);
	}

	/** The room a device belongs to, or null if unassigned. */
	groupOf(deviceId: string): Group | null {
		return this.groups.find((g) => g.device_ids.includes(deviceId)) ?? null;
	}

	async toggleFavorite(deviceId: string) {
		const prev = this.favorites;
		this.favorites = this.isFavorite(deviceId)
			? this.favorites.filter((id) => id !== deviceId)
			: [...this.favorites, deviceId];
		try {
			this.favorites = (await setFavorites(this.favorites)).device_ids;
		} catch (e) {
			this.favorites = prev;
			toasts.push(message(e), 'error');
		}
	}

	async createRoom(name: string): Promise<Group | null> {
		try {
			const group = await createGroup(name);
			this.groups = [...this.groups, group];
			return group;
		} catch (e) {
			toasts.push(message(e), 'error');
			return null;
		}
	}

	async renameRoom(id: string, name: string) {
		const prev = this.groups;
		this.groups = this.groups.map((g) => (g.id === id ? { ...g, name } : g));
		try {
			await updateGroup(id, { name });
		} catch (e) {
			this.groups = prev;
			toasts.push(message(e), 'error');
		}
	}

	async deleteRoom(id: string) {
		const prev = this.groups;
		this.groups = this.groups.filter((g) => g.id !== id);
		try {
			await deleteGroup(id);
		} catch (e) {
			this.groups = prev;
			toasts.push(message(e), 'error');
		}
	}

	/**
	 * Move a device into a room (or out of all rooms when groupId is null). A
	 * device lives in at most one room, so this drops it from its current room
	 * and adds it to the target, persisting only the rooms that changed.
	 */
	async assignDevice(deviceId: string, groupId: string | null) {
		const current = this.groupOf(deviceId);
		if ((current?.id ?? null) === groupId) return;

		const prev = this.groups;
		const changed: string[] = [];
		this.groups = this.groups.map((g) => {
			if (g.id === current?.id) {
				changed.push(g.id);
				return { ...g, device_ids: g.device_ids.filter((d) => d !== deviceId) };
			}
			if (g.id === groupId && !g.device_ids.includes(deviceId)) {
				changed.push(g.id);
				return { ...g, device_ids: [...g.device_ids, deviceId] };
			}
			return g;
		});

		try {
			await Promise.all(
				changed.map((id) => {
					const g = this.groups.find((x) => x.id === id)!;
					return updateGroup(id, { device_ids: g.device_ids });
				})
			);
		} catch (e) {
			this.groups = prev;
			toasts.push(message(e), 'error');
		}
	}
}

export const groupStore = new GroupsStore();
