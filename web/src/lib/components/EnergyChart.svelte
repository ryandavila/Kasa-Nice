<script lang="ts">
	import type { UsageStat } from '$lib/api/types';

	let { data, height = 128 }: { data: UsageStat[]; height?: number } = $props();

	const max = $derived(Math.max(0, ...data.map((d) => d.kwh)));
	// Label every bar when sparse, otherwise thin them out to avoid crowding.
	const step = $derived(Math.max(1, Math.ceil(data.length / 8)));
</script>

{#if data.length}
	<div class="flex items-end gap-1" style="height: {height}px">
		{#each data as d, i (d.label + i)}
			<div class="group relative flex h-full flex-1 items-end">
				<div
					class="w-full rounded-t-[3px] bg-accent/70 transition-[height,background-color] duration-300 group-hover:bg-accent"
					style="height: {max ? Math.max((d.kwh / max) * 100, d.kwh > 0 ? 2 : 0) : 0}%"
				></div>
				<!-- value tooltip on hover -->
				<div
					class="pointer-events-none absolute -top-7 left-1/2 z-10 -translate-x-1/2 whitespace-nowrap rounded-md border border-line bg-surface px-2 py-1 font-mono text-[10px] text-ink opacity-0 shadow-sm transition-opacity group-hover:opacity-100"
				>
					{d.label} · {d.kwh.toFixed(2)} kWh
				</div>
			</div>
		{/each}
	</div>
	<div class="mt-1.5 flex gap-1">
		{#each data as d, i (d.label + i)}
			<span class="flex-1 text-center font-mono text-[9px] text-faint">
				{i % step === 0 ? d.label : ''}
			</span>
		{/each}
	</div>
{:else}
	<p class="py-8 text-center text-sm text-muted">No history reported yet.</p>
{/if}
