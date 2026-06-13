<script lang="ts" module>
	export type IconName =
		| 'Bulb'
		| 'LightStrip'
		| 'Dimmer'
		| 'Strip'
		| 'Plug'
		| 'Unknown'
		| 'power'
		| 'refresh'
		| 'radar'
		| 'sun'
		| 'moon'
		| 'droplet'
		| 'bolt'
		| 'chart'
		| 'search';

	// 24×24 stroke icons. Each entry is the inner markup of an <svg>.
	const PATHS: Record<IconName, string> = {
		Bulb: '<path d="M9 18h6m-5 3h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.2 1 2v.3h6v-.3c0-.8.4-1.5 1-2A7 7 0 0 0 12 2Z"/>',
		LightStrip:
			'<path d="M3 14c2.5 0 2.5-4 5-4s2.5 4 5 4 2.5-4 5-4 2.5 4 4 4"/><circle cx="6" cy="12" r=".6" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r=".6" fill="currentColor" stroke="none"/><circle cx="18" cy="12" r=".6" fill="currentColor" stroke="none"/>',
		Dimmer:
			'<circle cx="12" cy="12" r="8"/><path d="M12 12 8.5 8.5"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/>',
		Strip:
			'<rect x="2.5" y="8" width="19" height="8" rx="2"/><path d="M7 8V6m5 2V6m5 2V6"/><circle cx="7" cy="12" r="1.1"/><circle cx="12" cy="12" r="1.1"/><circle cx="17" cy="12" r="1.1"/>',
		Plug: '<path d="M9 2v6m6-6v6M6 8h12v2a6 6 0 0 1-6 6 6 6 0 0 1-6-6V8Zm6 8v6"/>',
		Unknown:
			'<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.4 2.3c-.6.3-.9.8-.9 1.4v.3m0 3h.01"/>',
		power: '<path d="M12 3v9m5.6-6.4a8 8 0 1 1-11.2 0"/>',
		refresh: '<path d="M3 12a9 9 0 0 1 15.5-6.3M21 4v4h-4M21 12a9 9 0 0 1-15.5 6.3M3 20v-4h4"/>',
		radar:
			'<path d="M12 12 19 5M12 3a9 9 0 1 0 9 9"/><path d="M12 7.5a4.5 4.5 0 1 0 4.5 4.5" opacity=".55"/>',
		sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19"/>',
		moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5Z"/>',
		droplet: '<path d="M12 3.5s6 6.3 6 10.5a6 6 0 0 1-12 0c0-4.2 6-10.5 6-10.5Z"/>',
		bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/>',
		chart: '<path d="M4 20V4m16 16H4m4-2V11m5 7V7m5 11v-5"/>',
		search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>'
	};

	export const ICONS = PATHS;
</script>

<script lang="ts">
	let {
		name,
		size = 24,
		stroke = 1.6,
		class: cls = ''
	}: { name: IconName; size?: number; stroke?: number; class?: string } = $props();
</script>

<svg
	width={size}
	height={size}
	viewBox="0 0 24 24"
	fill="none"
	stroke="currentColor"
	stroke-width={stroke}
	stroke-linecap="round"
	stroke-linejoin="round"
	class={cls}
	aria-hidden="true"
>
	<!-- eslint-disable-next-line svelte/no-at-html-tags -- PATHS is a static, in-module constant -->
	{@html PATHS[name]}
</svg>
