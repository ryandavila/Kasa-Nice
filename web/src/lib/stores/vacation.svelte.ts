import { getVacation, setVacation, errorMessage as message } from '$lib/api/client';
import type { VacationConfig, VacationStatus } from '$lib/api/types';
import { toasts } from './toasts.svelte';

/**
 * Vacation mode (presence simulation). Unlike the list-shaped stores this holds
 * ONE config document plus the server's live status (whether the window is
 * active and the next planned switch). The backend owns the simulation; this
 * store is the editor over its config and the source for the header indicator.
 *
 * Saves are optimistic — the local config updates immediately and reverts on
 * failure — matching the groups/schedules stores. The server echoes back the
 * recomputed status (active flag, next-switch time, resolved device list), which
 * we trust over the optimistic guess.
 */

/** Built-in defaults so the UI has a coherent draft before the first load. */
const DEFAULTS: VacationStatus = {
	enabled: false,
	device_ids: [],
	room_ids: [],
	start_time: null,
	end_time: '23:00',
	min_interval_minutes: 15,
	max_interval_minutes: 45,
	active: false,
	next_switch_ts: null,
	resolved_device_ids: []
};

class VacationStore {
	status = $state<VacationStatus>({ ...DEFAULTS });
	loaded = $state(false);

	/** Whether the simulation is currently running its window (for the header dot). */
	get active(): boolean {
		return this.status.enabled && this.status.active;
	}

	async load() {
		try {
			this.status = await getVacation();
		} catch {
			// Vacation mode is best-effort in the UI; a load failure shows defaults.
		} finally {
			this.loaded = true;
		}
	}

	/** The editable config subset of the current status (drops server-only fields). */
	config(): VacationConfig {
		const s = this.status;
		return {
			enabled: s.enabled,
			device_ids: s.device_ids,
			room_ids: s.room_ids,
			start_time: s.start_time,
			end_time: s.end_time,
			min_interval_minutes: s.min_interval_minutes,
			max_interval_minutes: s.max_interval_minutes
		};
	}

	/** Full-replace the config, reverting the optimistic change if it fails. */
	async save(config: VacationConfig): Promise<boolean> {
		const prev = this.status;
		// Optimistic: reflect the new config at once; the server fills in status.
		this.status = { ...this.status, ...config };
		try {
			this.status = await setVacation(config);
			return true;
		} catch (e) {
			this.status = prev;
			toasts.push(message(e), 'error');
			return false;
		}
	}

	/** Flip the enabled flag (the common one-tap action) and persist it. */
	async toggleEnabled() {
		await this.save({ ...this.config(), enabled: !this.status.enabled });
	}
}

export const vacationStore = new VacationStore();
