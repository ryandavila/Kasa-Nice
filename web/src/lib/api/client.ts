import type {
	Alert,
	AlertThresholds,
	BackupDocument,
	Device,
	EnergyHistory,
	EnergyInsights,
	EnergySummary,
	Favorites,
	Group,
	Hsv,
	PowerResult,
	Scene,
	SceneApplyResult,
	SceneEntry,
	Schedule,
	ScheduleCreate,
	ScheduleUpdate,
	ServerConfig,
	ServerStatus,
	Usage,
	VacationConfig,
	VacationStatus
} from './types';

/**
 * Thin client for the Kasa-Nice FastAPI backend. Paths are relative so the same
 * code works behind the Vite dev proxy and when served by the backend in prod.
 */

const BASE = '/api';

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string
	) {
		super(message);
		this.name = 'ApiError';
	}
}

/**
 * Human-readable message for any thrown value. ApiError carries the server's
 * detail string; anything else falls back generically. Shared by every store's
 * toast handling so the wording can't drift per feature.
 */
export function errorMessage(e: unknown, fallback = 'Something went wrong'): string {
	if (e instanceof ApiError) return e.message;
	return e instanceof Error ? e.message : fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const res = await fetch(`${BASE}${path}`, {
		headers: { 'Content-Type': 'application/json' },
		...init
	});
	if (!res.ok) {
		let detail = res.statusText;
		try {
			const body = await res.json();
			detail = body.detail ?? detail;
		} catch {
			// non-JSON error body; fall back to status text
		}
		throw new ApiError(res.status, detail);
	}
	if (res.status === 204) return undefined as T;
	return res.json() as Promise<T>;
}

function post<T>(path: string, body?: unknown): Promise<T> {
	return request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined });
}

function patch<T>(path: string, body: unknown): Promise<T> {
	return request<T>(path, { method: 'PATCH', body: JSON.stringify(body) });
}

function put<T>(path: string, body: unknown): Promise<T> {
	return request<T>(path, { method: 'PUT', body: JSON.stringify(body) });
}

function del(path: string): Promise<void> {
	return request<void>(path, { method: 'DELETE' });
}

/** Devices discovered at startup / cached by the backend. */
export const listDevices = () => request<Device[]>('/devices');

/** Cached devices with live state re-read from the hardware; used for polling. */
export const getState = () => request<Device[]>('/state');

/** Trigger a fresh network discovery; pass a target IP to probe a single host. */
export const discoverDevices = (target?: string) => post<Device[]>('/discover', { target });

/** Server config the UI needs, e.g. the default subnet to offer for sweeping. */
export const getConfig = () => request<ServerConfig>('/config');

/** Live server status (whether the startup sweep is still running). */
export const getStatus = () => request<ServerStatus>('/status');

/** Unicast-sweep a whole subnet; falls back to the server's configured subnet. */
export const scanSubnet = (subnet?: string) => post<Device[]>('/discover/subnet', { subnet });

export const setPower = (id: string, on: boolean) =>
	post<Device>(`/devices/${encodeURIComponent(id)}/power`, { on });

export const setBrightness = (id: string, value: number) =>
	post<Device>(`/devices/${encodeURIComponent(id)}/brightness`, { value });

export const setColorHex = (id: string, hex: string) =>
	post<Device>(`/devices/${encodeURIComponent(id)}/color`, { hex });

export const setColorHsv = (id: string, hsv: Hsv) =>
	post<Device>(`/devices/${encodeURIComponent(id)}/color`, { hsv });

/** Energy-monitoring data (live power + daily/monthly history) for a device. */
export const getUsage = (id: string) => request<Usage>(`/devices/${encodeURIComponent(id)}/usage`);

/** Whole-home energy totals aggregated across all metered devices. */
export const getEnergySummary = () => request<EnergySummary>('/energy/summary');

/** Derived energy insights: month projection, room rollups, week delta, idle draw. */
export const getEnergyInsights = () => request<EnergyInsights>('/energy/insights');

export const setChildPower = (id: string, childId: string, on: boolean) =>
	post<Device>(`/devices/${encodeURIComponent(id)}/children/${encodeURIComponent(childId)}/power`, {
		on
	});

/** Rename a device; returns the updated device (new alias, unchanged id). */
export const renameDevice = (id: string, alias: string) =>
	patch<Device>(`/devices/${encodeURIComponent(id)}`, { alias });

/** Rename one outlet of a strip; returns the updated parent device. */
export const renameChild = (id: string, childId: string, alias: string) =>
	patch<Device>(`/devices/${encodeURIComponent(id)}/children/${encodeURIComponent(childId)}`, {
		alias
	});

/** Switch every device in a room at once; reports per-device success/failure. */
export const setGroupPower = (id: string, on: boolean) =>
	post<PowerResult>(`/groups/${encodeURIComponent(id)}/power`, { on });

/** Switch every known device at once (e.g. "everything off"). */
export const setAllPower = (on: boolean) => post<PowerResult>('/power', { on });

/** Persisted energy history (recent power samples + daily totals) for a device. */
export const getHistory = (id: string, hours = 24, days = 30) =>
	request<EnergyHistory>(`/devices/${encodeURIComponent(id)}/history?hours=${hours}&days=${days}`);

// ── Groups (rooms) & favorites ──────────────────────────────────────────────

export const listGroups = () => request<Group[]>('/groups');

export const createGroup = (name: string) => post<Group>('/groups', { name });

export const updateGroup = (id: string, patch_: { name?: string; device_ids?: string[] }) =>
	patch<Group>(`/groups/${encodeURIComponent(id)}`, patch_);

export const deleteGroup = (id: string) => del(`/groups/${encodeURIComponent(id)}`);

export const getFavorites = () => request<Favorites>('/favorites');

export const setFavorites = (deviceIds: string[]) =>
	put<Favorites>('/favorites', { device_ids: deviceIds });

// ── Schedules (timers) ────────────────────────────────────────────────────────

export const listSchedules = () => request<Schedule[]>('/schedules');

export const createSchedule = (rule: ScheduleCreate) => post<Schedule>('/schedules', rule);

export const updateSchedule = (id: string, patch_: ScheduleUpdate) =>
	patch<Schedule>(`/schedules/${encodeURIComponent(id)}`, patch_);

export const deleteSchedule = (id: string) => del(`/schedules/${encodeURIComponent(id)}`);

// ── Alerts ──────────────────────────────────────────────────────────────────

/** Recent alerts from the server's in-memory ring buffer (newest first). */
export const getRecentAlerts = () => request<Alert[]>('/alerts/recent');

/** Read the per-device power-draw thresholds (device_id -> watts). */
export const getAlertThresholds = () => request<AlertThresholds>('/alerts/thresholds');

/** Full-replace the per-device power-draw thresholds; returns the sanitized map. */
export const setAlertThresholds = (thresholds: Record<string, number>) =>
	put<AlertThresholds>('/alerts/thresholds', { thresholds });

// ── Scenes ──────────────────────────────────────────────────────────────────

export const listScenes = () => request<Scene[]>('/scenes');

/**
 * Create a scene either from explicit `entries` or by snapshotting the current
 * state of `device_ids` (the server captures on/off + brightness/color). Exactly
 * one source may be given, mirroring the backend's validation.
 */
export const createScene = (
	name: string,
	source: { entries: SceneEntry[] } | { device_ids: string[] }
) => post<Scene>('/scenes', { name, ...source });

export const updateScene = (id: string, patch_: { name?: string; entries?: SceneEntry[] }) =>
	patch<Scene>(`/scenes/${encodeURIComponent(id)}`, patch_);

export const deleteScene = (id: string) => del(`/scenes/${encodeURIComponent(id)}`);

/** Apply a scene; reports which devices reached their saved state and which failed. */
export const applyScene = (id: string) =>
	post<SceneApplyResult>(`/scenes/${encodeURIComponent(id)}/apply`);

// ── Backup & restore ──────────────────────────────────────────────────────────

/** Fetch the current backup document (every JSON store, one versioned object). */
export const getBackup = () => request<BackupDocument>('/backup');

/**
 * Replace every JSON store's contents from a backup document. The server
 * validates the whole payload before writing anything, so a bad file 4xxs with
 * no partial effect — see the confirmation step in `SettingsPanel`.
 */
export const restoreBackup = (doc: BackupDocument) => post<BackupDocument>('/backup/restore', doc);

/**
 * Trigger a browser download of a URL under `/api` by creating a throwaway
 * `<a download>` and clicking it — the standard DOM technique for saving a
 * fetched blob without navigating away from the SPA. Shared by the backup JSON
 * and energy-history DB downloads, which otherwise differ only in URL/filename.
 */
async function downloadFile(path: string, filename: string): Promise<void> {
	const res = await fetch(`${BASE}${path}`);
	if (!res.ok) {
		let detail = res.statusText;
		try {
			detail = ((await res.json()).detail as string) ?? detail;
		} catch {
			// non-JSON error body; fall back to status text
		}
		throw new ApiError(res.status, detail);
	}
	const blob = await res.blob();
	const url = URL.createObjectURL(blob);
	try {
		const a = document.createElement('a');
		a.href = url;
		a.download = filename;
		a.click();
	} finally {
		// Revoke after the click has had a chance to start the download; an
		// immediate revoke can race the browser's own read of the blob URL.
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}
}

/** Download the backup document as a file (browser save dialog / downloads folder). */
export const downloadBackupFile = () => downloadFile('/backup', 'kasa-nice-backup.json');

/** Download a consistent snapshot of the energy-history SQLite database. */
export const downloadEnergyDb = () =>
	downloadFile('/backup/energy.db', 'kasa-nice-energy-history.db');
// ── Vacation mode (presence simulation) ─────────────────────────────────────

/** Read the vacation config plus live engine status (active + next switch). */
export const getVacation = () => request<VacationStatus>('/vacation');

/** Full-replace the vacation config; returns the saved config with fresh status. */
export const setVacation = (config: VacationConfig) => put<VacationStatus>('/vacation', config);
