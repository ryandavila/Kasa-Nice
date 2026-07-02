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
	/**
	 * Whether this device (and its outlets) can be renamed via the API. False for
	 * cloud-only devices (e.g. HS300 strips); the UI hides the rename affordance.
	 */
	can_rename: boolean;
	/**
	 * False for a known device that didn't answer discovery: rendered as a grayed,
	 * non-interactive card so it doesn't vanish from rooms/favorites. The retry
	 * affordance flips it back once the device answers.
	 */
	reachable: boolean;
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

// ── Schedules (timers) ────────────────────────────────────────────────────────

/** What a schedule rule acts on: a single device or a whole room (group). */
export interface ScheduleTarget {
	type: 'device' | 'room';
	/** Device id or group id, per `type`. */
	id: string;
}

export type ScheduleAction = 'on' | 'off';

/** Audit note for a rule's most recent firing (server-written, read-only). */
export interface LastFired {
	/** Unix epoch seconds of the last firing attempt. */
	ts: number;
	/** Human-readable outcome, e.g. 'ok' or 'partial: 1 failed'. */
	result: string;
}

/**
 * A fixed-time rule: at `time` on `days`, apply `action` to `target`. `kind` is
 * a discriminator fixed to 'fixed_time' in v1, left open so future rule kinds
 * (sunrise/sunset, one-shot timers) can be added without reshaping these.
 */
export interface Schedule {
	id: string;
	kind: 'fixed_time';
	enabled: boolean;
	/** Local wall-clock time, 'HH:MM'. */
	time: string;
	/** Weekdays the rule fires on; 0=Monday … 6=Sunday. */
	days: number[];
	target: ScheduleTarget;
	action: ScheduleAction;
	/** Null until the rule first fires. */
	last_fired: LastFired | null;
}

/** Fields a client supplies to create a rule; the server assigns id/last_fired. */
export interface ScheduleCreate {
	enabled?: boolean;
	time: string;
	days: number[];
	target: ScheduleTarget;
	action: ScheduleAction;
}

/** Partial update of a rule; omitted fields are left unchanged. */
export interface ScheduleUpdate {
	enabled?: boolean;
	time?: string;
	days?: number[];
	target?: ScheduleTarget;
	action?: ScheduleAction;
}

// ── Scenes ──────────────────────────────────────────────────────────────────

/** A device's saved state within a scene; brightness/hsv only apply to lights. */
export interface SceneEntryState {
	on: boolean;
	/** Target brightness 0-100; omitted/null for non-dimmable devices. */
	brightness?: number | null;
	/** Target color; omitted/null for non-color devices. */
	hsv?: Hsv | null;
}

/** One device's target state within a scene, keyed by stable device id. */
export interface SceneEntry {
	device_id: string;
	state: SceneEntryState;
}

/** A named preset: a set of per-device states applied together as one action. */
export interface Scene {
	id: string;
	name: string;
	entries: SceneEntry[];
}

/** Outcome of applying a scene: which devices reached their saved state. */
export interface SceneApplyResult {
	/** Device ids that reached their saved state. */
	succeeded: string[];
	/** Device ids that couldn't be set (offline, unknown, or a failed step). */
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

// ── Energy insights ─────────────────────────────────────────────────────────

/**
 * Month-to-date energy plus a naive linear month-end projection (MTD daily
 * average extrapolated across every day of the month — a forecast, not a bill).
 */
export interface MonthProjection {
	/** Whole-home energy used so far this calendar month, in kWh. */
	month_to_date_kwh: number;
	/** Extrapolated whole-home energy for the full month, in kWh. */
	projected_kwh: number;
	/** MTD kWh × flat rate; null when no rate is configured. */
	month_to_date_cost: number | null;
	/** Projected kWh × flat rate; null when no rate is configured. */
	projected_cost: number | null;
}

/** Today/month energy rolled up over one room (or the "unassigned" bucket). */
export interface RoomUsage {
	/** Group id, or the synthetic "unassigned" for room-less devices. */
	group_id: string;
	name: string;
	today_kwh: number;
	month_kwh: number;
	today_cost: number | null;
	month_cost: number | null;
}

/** Whole-home kWh for the current ISO week vs the previous full week. */
export interface WeekComparison {
	this_week_kwh: number;
	last_week_kwh: number;
}

/** A device's overnight (01:00–05:00 local) median standing power draw. */
export interface IdleDevice {
	device_id: string;
	/** Live alias when the device is still known; otherwise its id. */
	alias: string;
	/** Median overnight power draw, in watts. */
	idle_w: number;
	/** True when the idle draw exceeds the vampire-load threshold. */
	is_idle_hog: boolean;
}

/** Derived energy insights across all recorded devices. */
export interface EnergyInsights {
	projection: MonthProjection;
	rooms: RoomUsage[];
	week: WeekComparison;
	idle: IdleDevice[];
}

// ── Alerts ──────────────────────────────────────────────────────────────────

export type AlertType = 'device_unreachable' | 'device_recovered' | 'power_exceeded';

/** One delivered alert from the server's recent-alerts ring buffer. */
export interface Alert {
	id: string;
	/** Unix epoch seconds when the alert fired. */
	ts: number;
	type: AlertType;
	device_id: string;
	/** Human-readable message (also the webhook body). */
	message: string;
	/** Live draw at the time, for `power_exceeded` alerts only. */
	power_w: number | null;
	/** The configured threshold, for `power_exceeded` alerts only. */
	threshold_w: number | null;
}

/** Per-device power-draw thresholds in watts (device_id -> watts); a full-replace map. */
export interface AlertThresholds {
	thresholds: Record<string, number>;
}
