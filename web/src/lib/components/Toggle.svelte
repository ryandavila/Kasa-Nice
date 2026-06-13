<script lang="ts">
	let {
		checked,
		onchange,
		label,
		disabled = false,
		size = 'md'
	}: {
		checked: boolean;
		onchange: (on: boolean) => void;
		label: string;
		disabled?: boolean;
		size?: 'sm' | 'md';
	} = $props();

	const dims = $derived(
		size === 'sm'
			? { w: 'w-9', h: 'h-5', knob: 'h-3.5 w-3.5', on: 'translate-x-4', off: 'translate-x-0.5' }
			: { w: 'w-12', h: 'h-7', knob: 'h-5 w-5', on: 'translate-x-5', off: 'translate-x-1' }
	);
</script>

<button
	type="button"
	role="switch"
	aria-checked={checked}
	aria-label={label}
	{disabled}
	onclick={() => onchange(!checked)}
	class="{dims.w} {dims.h} relative shrink-0 rounded-full border transition-colors duration-300 disabled:opacity-40
		{checked ? 'border-accent bg-accent' : 'border-line bg-raised'}"
	style={checked ? 'box-shadow: 0 0 0 4px var(--accent-soft), 0 0 14px -2px var(--glow);' : ''}
>
	<span
		class="{dims.knob} absolute top-1/2 -translate-y-1/2 rounded-full bg-white shadow-sm transition-transform duration-300
			{checked ? dims.on : dims.off}"
		style="transition-timing-function: cubic-bezier(0.34, 1.56, 0.64, 1);"
	></span>
</button>
