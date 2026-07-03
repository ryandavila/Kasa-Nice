<script lang="ts">
	import { onMount } from 'svelte';
	import type { AlertType } from '$lib/api/types';
	import { dismissable } from '$lib/actions/dismissable';
	import { alertStore } from '$lib/stores/alerts.svelte';
	import { deviceStore } from '$lib/stores/devices.svelte';

	// Local dropdown state. The bell lives in the header; the panel is anchored to
	// it and closes on an outside click or Escape (the dismissable action).
	let open = $state(false);

	// Only metered, reachable devices can have a wattage threshold; an unreachable
	// or unmetered device has no live draw to compare against.
	const metered = $derived(deviceStore.devices.filter((d) => d.has_emeter && d.reachable));

	function toggle() {
		open = !open;
		// Opening the panel is the "seen" signal, so the badge clears on view.
		if (open) alertStore.markSeen();
	}

	// Per-type presentation: a dot color and short label. Kept here (not in the
	// store) since it's purely visual.
	const META: Record<AlertType, { label: string; dot: string }> = {
		device_unreachable: { label: 'Unreachable', dot: 'bg-red-500' },
		device_recovered: { label: 'Recovered', dot: 'bg-accent' },
		power_exceeded: { label: 'High power', dot: 'bg-amber-500' }
	};

	function when(ts: number): string {
		return new Date(ts * 1000).toLocaleString(undefined, {
			month: 'short',
			day: 'numeric',
			hour: '2-digit',
			minute: '2-digit'
		});
	}

	function onThresholdChange(deviceId: string, raw: string) {
		const watts = Number.parseFloat(raw);
		if (Number.isNaN(watts) || watts <= 0) alertStore.clearThreshold(deviceId);
		else alertStore.setThreshold(deviceId, watts);
	}

	onMount(() => {
		alertStore.startPolling();
		alertStore.loadThresholds();
		return () => alertStore.stopPolling();
	});
</script>

<div class="relative" use:dismissable={() => (open = false)}>
	<button
		type="button"
		onclick={toggle}
		aria-label="Alerts"
		aria-expanded={open}
		title="Alerts"
		class="relative grid h-10 w-10 place-items-center rounded-full border border-line bg-surface text-muted transition-colors hover:border-accent hover:text-accent-ink"
	>
		<!-- Inline bell (kept local so the shared Icon set stays untouched). -->
		<svg
			width="18"
			height="18"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			stroke-width="1.6"
			stroke-linecap="round"
			stroke-linejoin="round"
			aria-hidden="true"
		>
			<path d="M6 9a6 6 0 0 1 12 0c0 4 1 5 2 6H4c1-1 2-2 2-6Z" />
			<path d="M10 20a2 2 0 0 0 4 0" />
		</svg>
		{#if alertStore.unseen > 0}
			<span
				class="absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-red-500 px-1 text-[10px] font-semibold leading-none text-white tabular-nums"
			>
				{alertStore.unseen > 9 ? '9+' : alertStore.unseen}
			</span>
		{/if}
	</button>

	{#if open}
		<div
			class="animate-rise absolute right-0 z-30 mt-2 w-80 overflow-hidden rounded-card border border-line bg-surface shadow-[0_18px_40px_-20px_var(--glow)]"
		>
			<!-- ── Recent alerts ─────────────────────────────────────────────── -->
			<div class="flex items-center justify-between border-b border-line px-4 py-2.5">
				<span class="font-display text-sm font-semibold text-ink">Alerts</span>
				<span class="font-mono text-xs text-faint">{alertStore.alerts.length}</span>
			</div>

			<div class="max-h-72 overflow-y-auto">
				{#if alertStore.alerts.length === 0}
					<p class="px-4 py-6 text-center text-sm text-muted">No alerts yet.</p>
				{:else}
					<ul class="divide-y divide-line">
						{#each alertStore.alerts as alert (alert.id)}
							{@const meta = META[alert.type]}
							<li class="flex items-start gap-2.5 px-4 py-2.5">
								<span class="mt-1.5 h-2 w-2 shrink-0 rounded-full {meta.dot}" aria-hidden="true"
								></span>
								<div class="min-w-0 grow">
									<p class="text-sm leading-snug text-ink">{alert.message}</p>
									<p class="mt-0.5 text-xs text-faint">
										<span class="font-medium text-muted">{meta.label}</span>
										· {when(alert.ts)}
									</p>
								</div>
							</li>
						{/each}
					</ul>
				{/if}
			</div>

			<!-- ── Power-draw thresholds ─────────────────────────────────────── -->
			<div class="border-t border-line bg-raised/40 px-4 py-3">
				<p class="mb-2 font-display text-xs font-semibold uppercase tracking-[0.14em] text-muted">
					Power alerts
				</p>
				{#if metered.length === 0}
					<p class="text-xs text-faint">No metered devices to watch.</p>
				{:else}
					<ul class="space-y-2">
						{#each metered as device (device.id)}
							<li class="flex items-center justify-between gap-2">
								<span class="min-w-0 truncate text-sm text-ink">{device.alias}</span>
								<span class="flex shrink-0 items-center gap-1 text-xs text-faint">
									<label class="sr-only" for="threshold-{device.id}">
										Power threshold for {device.alias} in watts
									</label>
									<input
										id="threshold-{device.id}"
										type="number"
										min="0"
										step="1"
										inputmode="numeric"
										placeholder="—"
										value={alertStore.thresholds[device.id] ?? ''}
										onchange={(e) => onThresholdChange(device.id, e.currentTarget.value)}
										class="w-16 rounded-md border border-line bg-surface px-2 py-1 text-right text-sm text-ink outline-none transition-colors focus:border-accent"
									/>
									W
								</span>
							</li>
						{/each}
					</ul>
					<p class="mt-2 text-xs text-faint">
						Alert when a device draws more than its set wattage. Blank = off.
					</p>
				{/if}
			</div>
		</div>
	{/if}
</div>
