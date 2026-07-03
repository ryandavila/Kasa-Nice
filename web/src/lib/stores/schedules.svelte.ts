import {
	listSchedules,
	createSchedule,
	updateSchedule,
	deleteSchedule,
	errorMessage as message
} from '$lib/api/client';
import type { Schedule, ScheduleCreate, ScheduleUpdate } from '$lib/api/types';
import { toasts } from './toasts.svelte';

/**
 * Server-side schedule rules ("at HH:MM on these days, turn X on/off"). The
 * backend owns evaluation and firing; this store is just the editor over the
 * rule list. Edits are optimistic — applied locally at once, persisted in the
 * background, and reverted with a toast on failure — mirroring the groups store.
 */
class SchedulesStore {
	rules = $state<Schedule[]>([]);
	loaded = $state(false);

	async load() {
		try {
			this.rules = await listSchedules();
		} catch {
			// Schedules are best-effort in the UI; a load failure just shows empty.
		} finally {
			this.loaded = true;
		}
	}

	async create(rule: ScheduleCreate): Promise<Schedule | null> {
		try {
			const created = await createSchedule(rule);
			this.rules = [...this.rules, created];
			return created;
		} catch (e) {
			toasts.push(message(e), 'error');
			return null;
		}
	}

	/** Apply a partial update, reverting the optimistic change if it fails. */
	async update(id: string, patch: ScheduleUpdate): Promise<Schedule | null> {
		const prev = this.rules;
		this.rules = this.rules.map((r) => (r.id === id ? { ...r, ...patch } : r));
		try {
			const updated = await updateSchedule(id, patch);
			// Trust the server's normalized copy (sorted days, stamped fields).
			this.rules = this.rules.map((r) => (r.id === id ? updated : r));
			return updated;
		} catch (e) {
			this.rules = prev;
			toasts.push(message(e), 'error');
			return null;
		}
	}

	/** Flip a rule's enabled flag (the common one-tap action). */
	toggleEnabled(id: string) {
		const rule = this.rules.find((r) => r.id === id);
		if (rule) this.update(id, { enabled: !rule.enabled });
	}

	async remove(id: string) {
		const prev = this.rules;
		this.rules = this.rules.filter((r) => r.id !== id);
		try {
			await deleteSchedule(id);
		} catch (e) {
			this.rules = prev;
			toasts.push(message(e), 'error');
		}
	}
}

export const scheduleStore = new SchedulesStore();
