import type { Device, Hsv, ServerConfig, ServerStatus, Usage } from './types';

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

export const setChildPower = (id: string, childId: string, on: boolean) =>
	post<Device>(`/devices/${encodeURIComponent(id)}/children/${encodeURIComponent(childId)}/power`, {
		on
	});
