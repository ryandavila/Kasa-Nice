/**
 * Null-safe display formatting for energy numbers and money, shared by the
 * energy components so the '—' placeholder, precision, and currency prefix
 * can't drift between panels showing the same figures.
 */

export function fmt(v: number | null, digits = 2): string {
	return v == null ? '—' : v.toFixed(digits);
}

export function fmtMoney(v: number | null, currency = '$'): string {
	return v == null ? '—' : currency + v.toFixed(2);
}
