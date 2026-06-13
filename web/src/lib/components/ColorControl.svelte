<script lang="ts">
	import type { Hsv } from '$lib/api/types';
	import { hsvToHex } from '$lib/utils/color';
	import Icon from './Icon.svelte';

	let {
		hsv,
		onchange,
		disabled = false
	}: { hsv: Hsv | null; onchange: (hex: string) => void; disabled?: boolean } = $props();

	const current = $derived(hsv ? hsvToHex(hsv) : '#4acbd6');
	let timer: ReturnType<typeof setTimeout>;

	function oninput(e: Event) {
		const hex = (e.target as HTMLInputElement).value;
		clearTimeout(timer);
		timer = setTimeout(() => onchange(hex), 200);
	}
</script>

<label
	class="group relative flex cursor-pointer items-center gap-2 rounded-full border border-line bg-raised py-1.5 pl-1.5 pr-3 text-xs font-medium text-muted transition-colors hover:border-accent has-[:disabled]:cursor-default has-[:disabled]:opacity-40"
>
	<span
		class="grid h-6 w-6 place-items-center rounded-full ring-1 ring-black/5"
		style="background:{current};"
	>
		<Icon name="droplet" size={13} class="text-white/85 mix-blend-overlay" />
	</span>
	Color
	<input
		type="color"
		value={current}
		{disabled}
		{oninput}
		class="absolute inset-0 cursor-pointer opacity-0"
		aria-label="Pick color"
	/>
</label>
