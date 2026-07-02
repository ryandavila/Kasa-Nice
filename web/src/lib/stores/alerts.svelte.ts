import { getRecentAlerts, getAlertThresholds, setAlertThresholds, ApiError } from '$lib/api/client';
import type { Alert } from '$lib/api/types';
import { toasts } from './toasts.svelte';

function message(e: unknown): string {
	if (e instanceof ApiError) return e.message;
	return e instanceof Error ? e.message : 'Something went wrong';
}

// The newest alert id the user has seen, persisted so the unseen badge survives
// reloads. Read defensively — private-mode / disabled storage just means the
// badge doesn't persist, never a crash.
const LAST_SEEN_KEY = 'kasa-alerts-last-seen';

function loadLastSeen(): string | null {
	try {
		return localStorage.getItem(LAST_SEEN_KEY);
	} catch {
		return null;
	}
}

/**
 * Recent alerts and per-device power thresholds. Alerts are polled from the
 * server's ring buffer (newest first); "seen" is purely client-side — we
 * remember the newest alert id the user has viewed and count anything ahead of
 * it as unseen, which is robust to same-second timestamps unlike a ts compare.
 * Thresholds are optimistic like favorites: edits apply immediately and persist
 * as a single full-replace map, reverting on failure.
 */
class AlertStore {
	alerts = $state<Alert[]>([]);
	thresholds = $state<Record<string, number>>({});
	lastSeenId = $state<string | null>(loadLastSeen());

	private pollTimer: ReturnType<typeof setInterval> | null = null;

	/** Alerts newer than the last one the user saw (drives the bell badge). */
	get unseen(): number {
		if (!this.alerts.length) return 0;
		if (!this.lastSeenId) return this.alerts.length;
		// Alerts are newest-first, so every entry before the last-seen id is unseen.
		const idx = this.alerts.findIndex((a) => a.id === this.lastSeenId);
		return idx === -1 ? this.alerts.length : idx;
	}

	/** Silently refresh the recent-alerts list; never disrupts the UI on failure. */
	async load() {
		try {
			this.alerts = await getRecentAlerts();
		} catch {
			// Best-effort: keep the last good list until the next poll succeeds.
		}
	}

	async loadThresholds() {
		try {
			this.thresholds = (await getAlertThresholds()).thresholds;
		} catch {
			// Best-effort; the editor just shows no thresholds until a poll succeeds.
		}
	}

	/** Begin polling recent alerts. Idempotent; loads once immediately. */
	startPolling(intervalMs = 30000) {
		if (this.pollTimer) return;
		this.load();
		this.pollTimer = setInterval(() => this.load(), intervalMs);
	}

	stopPolling() {
		if (this.pollTimer) {
			clearInterval(this.pollTimer);
			this.pollTimer = null;
		}
	}

	/** Mark every current alert as seen (call when the dropdown opens). */
	markSeen() {
		const newest = this.alerts[0]?.id;
		if (!newest) return;
		this.lastSeenId = newest;
		try {
			localStorage.setItem(LAST_SEEN_KEY, newest);
		} catch {
			// Storage unavailable — the badge just won't persist across reloads.
		}
	}

	/**
	 * Set (watts > 0) or clear (watts <= 0) a device's threshold, then persist the
	 * whole map. Optimistic: applies immediately and reverts if the PUT fails.
	 */
	async setThreshold(deviceId: string, watts: number) {
		const prev = this.thresholds;
		const next = { ...this.thresholds };
		if (watts > 0) next[deviceId] = watts;
		else delete next[deviceId];
		this.thresholds = next; // optimistic
		try {
			this.thresholds = (await setAlertThresholds(next)).thresholds;
		} catch (e) {
			this.thresholds = prev;
			toasts.push(message(e), 'error');
		}
	}

	clearThreshold(deviceId: string) {
		return this.setThreshold(deviceId, 0);
	}
}

export const alertStore = new AlertStore();
