export type DeviceType = 'Bulb' | 'Plug' | 'Dimmer' | 'Strip' | 'LightStrip' | 'Unknown';

/** A controllable outlet within a multi-plug power strip. */
export interface ChildPlug {
	id: string;
	alias: string;
	is_on: boolean;
}

/** Hue (0-360), saturation (0-100), value/brightness (0-100). */
export type Hsv = [number, number, number];

export interface Device {
	/** Stable identifier (the device's host) used in API paths. */
	id: string;
	alias: string;
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
}

export interface Usage {
	device_id: string;
	/** Instantaneous power draw in watts. */
	current_power_w: number | null;
	today_kwh: number | null;
	month_kwh: number | null;
	voltage: number | null;
	/** Energy per day for the current month. */
	daily: UsageStat[];
	/** Energy per month for the current year. */
	monthly: UsageStat[];
}
