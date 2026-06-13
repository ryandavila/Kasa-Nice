import type { Hsv } from '$lib/api/types';

/** HSV (h 0-360, s/v 0-100) → "#rrggbb". Mirrors the backend conversion. */
export function hsvToHex([h, s, v]: Hsv): string {
	const sat = s / 100;
	const val = v / 100;
	const c = val * sat;
	const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
	const m = val - c;
	const [r, g, b] = (
		h < 60
			? [c, x, 0]
			: h < 120
				? [x, c, 0]
				: h < 180
					? [0, c, x]
					: h < 240
						? [0, x, c]
						: h < 300
							? [x, 0, c]
							: [c, 0, x]
	).map((n) => Math.round((n + m) * 255));
	return `#${[r, g, b].map((n) => n.toString(16).padStart(2, '0')).join('')}`;
}

/** "#rrggbb" → HSV (h 0-360, s/v 0-100). Mirrors the backend conversion. */
export function hexToHsv(hex: string): Hsv {
	const clean = hex.replace('#', '');
	const r = parseInt(clean.slice(0, 2), 16) / 255;
	const g = parseInt(clean.slice(2, 4), 16) / 255;
	const b = parseInt(clean.slice(4, 6), 16) / 255;
	const max = Math.max(r, g, b);
	const min = Math.min(r, g, b);
	const d = max - min;
	let h = 0;
	if (d !== 0) {
		if (max === r) h = ((g - b) / d) % 6;
		else if (max === g) h = (b - r) / d + 2;
		else h = (r - g) / d + 4;
		h *= 60;
		if (h < 0) h += 360;
	}
	const s = max === 0 ? 0 : d / max;
	return [Math.round(h), Math.round(s * 100), Math.round(max * 100)];
}
