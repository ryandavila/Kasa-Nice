<script lang="ts">
	import type { EnergySample } from '$lib/api/types';

	let { samples, height = 96 }: { samples: EnergySample[]; height?: number } = $props();

	// Only points with a real reading can be plotted (narrow power_w to number).
	const pts = $derived(
		samples.filter((s): s is { ts: number; power_w: number } => s.power_w != null)
	);
	const peak = $derived(pts.length ? Math.max(...pts.map((p) => p.power_w)) : 0);
	const avg = $derived(pts.length ? pts.reduce((sum, p) => sum + p.power_w, 0) / pts.length : 0);

	// A fixed 100-wide viewBox stretched to the container (preserveAspectRatio
	// none): x by sample index, y inverted with a small headroom so the peak
	// isn't flush against the top edge.
	const max = $derived(Math.max(1, peak) * 1.1);
	function x(i: number) {
		return pts.length > 1 ? (i / (pts.length - 1)) * 100 : 0;
	}
	function y(w: number) {
		return height - (w / max) * height;
	}
	const line = $derived(pts.map((p, i) => `${x(i)},${y(p.power_w)}`).join(' '));
	const area = $derived(`0,${height} ${line} 100,${height}`);
</script>

{#if pts.length > 1}
	<div>
		<svg
			viewBox="0 0 100 {height}"
			preserveAspectRatio="none"
			width="100%"
			{height}
			class="overflow-visible"
			role="img"
			aria-label="Power draw over the last 24 hours"
		>
			<polygon points={area} fill="var(--accent-soft)" />
			<polyline
				points={line}
				fill="none"
				stroke="var(--accent)"
				stroke-width="1.5"
				vector-effect="non-scaling-stroke"
				stroke-linejoin="round"
				stroke-linecap="round"
			/>
		</svg>
		<div class="mt-1.5 flex justify-between font-mono text-[10px] text-faint">
			<span>avg {avg.toFixed(1)} W</span>
			<span>peak {peak.toFixed(1)} W</span>
		</div>
	</div>
{:else}
	<p class="py-8 text-center text-sm text-muted">Not enough samples recorded yet.</p>
{/if}
