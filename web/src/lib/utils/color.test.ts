import { describe, it, expect } from 'vitest';
import type { Hsv } from '$lib/api/types';
import { hsvToHex, hexToHsv } from './color';

describe('hsvToHex', () => {
	it('maps primary colors', () => {
		expect(hsvToHex([0, 100, 100])).toBe('#ff0000');
		expect(hsvToHex([120, 100, 100])).toBe('#00ff00');
		expect(hsvToHex([240, 100, 100])).toBe('#0000ff');
	});

	it('maps black and white', () => {
		expect(hsvToHex([0, 0, 0])).toBe('#000000');
		expect(hsvToHex([0, 0, 100])).toBe('#ffffff');
	});
});

describe('hexToHsv', () => {
	it('maps primary colors', () => {
		expect(hexToHsv('#ff0000')).toEqual([0, 100, 100]);
		expect(hexToHsv('#00ff00')).toEqual([120, 100, 100]);
		expect(hexToHsv('#0000ff')).toEqual([240, 100, 100]);
	});

	it('tolerates a missing leading hash', () => {
		expect(hexToHsv('ff0000')).toEqual([0, 100, 100]);
	});
});

describe('round trip', () => {
	it('survives hsv → hex → hsv for saturated colors', () => {
		const cases: Hsv[] = [
			[0, 100, 100],
			[60, 100, 100],
			[180, 100, 100],
			[300, 100, 100]
		];
		for (const hsv of cases) {
			expect(hexToHsv(hsvToHex(hsv))).toEqual(hsv);
		}
	});
});
