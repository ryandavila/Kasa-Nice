<script lang="ts">
	import Icon from './Icon.svelte';

	let {
		value,
		onchange,
		disabled = false
	}: { value: number; onchange: (v: number) => void; disabled?: boolean } = $props();

	// While dragging we show the local value; otherwise we follow the server prop.
	let dragging = $state<number | null>(null);
	const display = $derived(dragging ?? value);
	let timer: ReturnType<typeof setTimeout>;

	function oninput(e: Event) {
		const v = +(e.target as HTMLInputElement).value;
		dragging = v;
		clearTimeout(timer);
		timer = setTimeout(() => {
			onchange(v);
			dragging = null;
		}, 220);
	}

	const fill = $derived(
		`background: linear-gradient(to right, var(--accent) ${display}%, var(--line) ${display}%);`
	);
</script>

<div class="flex items-center gap-3">
	<Icon name="sun" size={16} class="text-faint" />
	<input
		type="range"
		min="0"
		max="100"
		value={display}
		{disabled}
		{oninput}
		class="range-teal grow disabled:opacity-40"
		style={fill}
		aria-label="Brightness"
	/>
	<span class="w-9 text-right font-mono text-xs text-muted tabular-nums">{display}%</span>
</div>
