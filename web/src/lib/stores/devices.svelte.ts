import {
	listDevices,
	getState,
	discoverDevices,
	setPower,
	setBrightness,
	setColorHex,
	setChildPower,
	ApiError
} from '$lib/api/client';
import type { Device, DeviceType } from '$lib/api/types';
import { toasts } from './toasts.svelte';

/** Display order for the grouped device sections. */
export const TYPE_ORDER: DeviceType[] = ['Bulb', 'LightStrip', 'Dimmer', 'Strip', 'Plug'];

type Status = 'idle' | 'loading' | 'ready' | 'error';

function message(e: unknown): string {
	if (e instanceof ApiError) return e.message;
	return e instanceof Error ? e.message : 'Something went wrong';
}

class DeviceStore {
	devices = $state<Device[]>([]);
	status = $state<Status>('idle');
	error = $state<string | null>(null);
	/** Device ids with an in-flight request, for per-card busy state. */
	busy = $state<Record<string, boolean>>({});
	/** Whether the last background poll reached the hub. */
	live = $state(true);

	private pollTimer: ReturnType<typeof setInterval> | null = null;

	get isEmpty() {
		return this.status === 'ready' && this.devices.length === 0;
	}

	private replace(updated: Device) {
		const i = this.devices.findIndex((d) => d.id === updated.id);
		if (i === -1) this.devices.push(updated);
		else this.devices[i] = updated;
	}

	/**
	 * Merge polled state into the current list in place, skipping devices with
	 * an in-flight request so a background poll never clobbers optimistic state.
	 */
	private merge(fresh: Device[]) {
		for (const f of fresh) {
			if (this.busy[f.id]) continue;
			const cur = this.devices.find((d) => d.id === f.id);
			if (!cur) {
				this.devices.push(f);
				continue;
			}
			cur.is_on = f.is_on;
			cur.brightness = f.brightness;
			cur.hsv = f.hsv;
			for (const fc of f.children) {
				const cc = cur.children.find((c) => c.id === fc.id);
				if (cc) cc.is_on = fc.is_on;
			}
		}
	}

	private async run(id: string, action: () => Promise<Device>, revert?: () => void) {
		this.busy[id] = true;
		try {
			this.replace(await action());
		} catch (e) {
			revert?.();
			toasts.push(message(e), 'error');
		} finally {
			this.busy[id] = false;
		}
	}

	async load() {
		this.status = 'loading';
		this.error = null;
		try {
			this.devices = await listDevices();
			this.status = 'ready';
		} catch (e) {
			this.error = message(e);
			this.status = 'error';
		}
	}

	/** Silently re-read live state from the hub; never disrupts the UI on failure. */
	async refresh() {
		try {
			this.merge(await getState());
			this.live = true;
		} catch {
			this.live = false; // next successful poll recovers
		}
	}

	/** Begin polling live device state. Idempotent. */
	startPolling(intervalMs = 5000) {
		if (this.pollTimer) return;
		this.pollTimer = setInterval(() => {
			if (this.status === 'ready') this.refresh();
		}, intervalMs);
	}

	stopPolling() {
		if (this.pollTimer) {
			clearInterval(this.pollTimer);
			this.pollTimer = null;
		}
	}

	/** Broadcast re-discovery; refreshes the whole list. */
	async rediscover() {
		this.status = 'loading';
		try {
			this.devices = await discoverDevices();
			this.status = 'ready';
			toasts.push(`Found ${this.devices.length} devices`, 'info');
		} catch (e) {
			this.error = message(e);
			this.status = 'error';
			toasts.push(message(e), 'error');
		}
	}

	/** Probe a single LAN address; merges results into the list. */
	async discoverTarget(target: string): Promise<Device[]> {
		const found = await discoverDevices(target);
		for (const d of found) this.replace(d);
		return found;
	}

	togglePower(device: Device, on: boolean) {
		const prev = device.is_on;
		device.is_on = on; // optimistic
		toasts.push(`${device.alias} ${on ? 'on' : 'off'}`, on ? 'on' : 'off');
		return this.run(
			device.id,
			() => setPower(device.id, on),
			() => (device.is_on = prev)
		);
	}

	setBrightness(device: Device, value: number) {
		device.brightness = value; // optimistic
		device.is_on = true;
		return this.run(device.id, () => setBrightness(device.id, value));
	}

	setColor(device: Device, hex: string) {
		device.is_on = true;
		return this.run(device.id, () => setColorHex(device.id, hex));
	}

	toggleChild(device: Device, childId: string, on: boolean) {
		const child = device.children.find((c) => c.id === childId);
		const prev = child?.is_on ?? false;
		if (child) child.is_on = on; // optimistic
		toasts.push(`${childId} ${on ? 'on' : 'off'}`, on ? 'on' : 'off');
		return this.run(
			device.id,
			() => setChildPower(device.id, childId, on),
			() => {
				if (child) child.is_on = prev;
			}
		);
	}
}

export const deviceStore = new DeviceStore();
