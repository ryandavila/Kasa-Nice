export type DeviceType = 'Bulb' | 'Plug' | 'Dimmer' | 'Strip' | 'LightStrip' | 'Unknown';

/** A controllable outlet within a multi-plug power strip. */
export interface ChildPlug {
	/** Stable outlet id (the child's device_id); used in API paths, not the alias. */
	id: string;
	alias: string;
	is_on: boolean;
}

/** Hue (0-360), saturation (0-100), value/brightness (0-100). */
export type Hsv = [number, number, number];

export interface Device {
	/** Stable identifier (normalized MAC, or host when unavailable) used in API paths. */
	id: string;
	alias: string;
	/** LAN address for connection/display; may change under DHCP, unlike `id`. */
	host: string;
	model: string;
	device_type: DeviceType;
	is_on: boolean;
	/** Supports color (HSV) control. */
	is_color: boolean;
	/** Supports brightness control. */
	is_dimmable: boolean;
	/** Exposes energy-monitoring data. */
	has_emeter: boolean;
	/** Current brightness 0-100, or null if not dimmable. */
	brightness: number | null;
	/** Current color, or null if not a color device. */
	hsv: Hsv | null;
	/** Individually controllable outlets, for power strips. */
	children: ChildPlug[];
}

/** One bar in an energy chart — a day or a month. */
export interface UsageStat {
	label: string;
	kwh: number;
	/** Flat-rate cost (kWh × rate); null when no rate is configured. */
	cost: number | null;
}

/** Live server state the UI polls to reflect background work. */
export interface ServerStatus {
	/** True while the initial network sweep is still running. */
	discovering: boolean;
	/** Devices currently known to the server. */
	device_count: number;
}

/** Server-side configuration the UI needs (e.g. the default subnet to sweep). */
export interface ServerConfig {
	/** CIDR the server sweeps by unicast, or null if unconfigured. */
	scan_subnet: string | null;
	/** Global flat $/kWh rate in currency units, or null when unset. */
	energy_rate: number | null;
	/** Currency symbol for cost display, e.g. "$" (default "$"). */
	energy_currency: string;
}

export interface Usage {
	device_id: string;
	/** Instantaneous power draw in watts. */
	current_power_w: number | null;
	today_kwh: number | null;
	month_kwh: number | null;
	/** Flat-rate cost for today's energy; null when no rate is configured. */
	today_cost: number | null;
	/** Flat-rate cost for the month's energy; null when no rate is configured. */
	month_cost: number | null;
	voltage: number | null;
	/** Energy per day for the current month. */
	daily: UsageStat[];
	/** Energy per month for the current year. */
	monthly: UsageStat[];
}

/** Whole-home energy totals aggregated across all metered devices. */
export interface EnergySummary {
	/** Sum of live power draw across all metered devices, in watts. */
	total_power_w: number;
	today_kwh: number;
	month_kwh: number;
	/** Flat-rate cost for today's energy; null when no rate is configured. */
	today_cost: number | null;
	/** Flat-rate cost for the month's energy; null when no rate is configured. */
	month_cost: number | null;
	/** Number of metered devices included in the totals. */
	device_count: number;
}

/** A user-defined room: a named, ordered set of device ids. */
export interface Group {
	id: string;
	name: string;
	device_ids: string[];
}

/** The device ids the user has starred for quick access. */
export interface Favorites {
	device_ids: string[];
}

/** Outcome of a fan-out power action across many devices (a room, or all of them). */
export interface PowerResult {
	on: boolean;
	/** Device ids that switched successfully. */
	succeeded: string[];
	/** Device ids that couldn't be switched (unreachable or no longer known). */
	failed: string[];
}

/** One persisted power reading: unix epoch seconds and watts (null if unread). */
export interface EnergySample {
	ts: number;
	power_w: number | null;
}

/** A persisted day's total energy (and optional flat-rate cost). */
export interface DailyEnergy {
	/** Local date, ISO 'YYYY-MM-DD'. */
	date: string;
	kwh: number;
	cost: number | null;
}

/** Recorded history for a device: recent power samples and daily totals. */
export interface EnergyHistory {
	device_id: string;
	samples: EnergySample[];
	daily: DailyEnergy[];
}
