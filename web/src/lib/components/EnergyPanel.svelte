<script lang="ts">
	import { onMount } from 'svelte';
	import type { Usage } from '$lib/api/types';
	import { getUsage, getConfig, ApiError } from '$lib/api/client';
	import { deviceStore } from '$lib/stores/devices.svelte';
	import Icon from './Icon.svelte';
	import EnergyChart from './EnergyChart.svelte';

	const meters = $derived(deviceStore.devices.filter((d) => d.has_emeter));

	let usage = $state<Record<string, Usage>>({});
	let errors = $state<Record<string, string>>({});
	let loading = $state<Record<string, boolean>>({});

	// Global flat $/kWh rate used to show money cost alongside kWh. The cost is a
	// flat-rate approximation (no tiered/time-of-use billing) computed server-side.
	let energyRate = $state<number | null>(null);
	let currency = $state('$');

	async function loadOne(id: string) {
		loading[id] = true;
		delete errors[id];
		try {
			usage[id] = await getUsage(id);
		} catch (e) {
			errors[id] = e instanceof ApiError ? e.message : 'Failed to read energy data';
		} finally {
			loading[id] = false;
		}
	}

	function loadAll() {
		for (const m of meters) loadOne(m.id);
	}

	onMount(() => {
		loadAll();
		getConfig()
			.then((cfg) => {
				energyRate = cfg.energy_rate;
				currency = cfg.energy_currency || '$';
			})
			.catch(() => {
				// config is best-effort; cost display just stays hidden
			});
	});

	function fmt(v: number | null, digits = 2): string {
		return v == null ? '—' : v.toFixed(digits);
	}

	function fmtMoney(v: number | null): string {
		return v == null ? '—' : currency + v.toFixed(2);
	}
</script>

{#if !meters.length}
	<div class="rounded-card border border-dashed border-line bg-surface/40 p-12 text-center">
		<span
			class="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-accent-soft text-accent-ink"
		>
			<Icon name="chart" size={26} />
		</span>
		<p class="mt-4 font-display text-lg text-ink">No energy monitoring</p>
		<p class="mx-auto mt-1 max-w-xs text-sm text-muted">
			None of your devices report power usage. Plugs like the KP125 and HS300 expose energy data.
		</p>
	</div>
{:else}
	<div class="space-y-6">
		{#each meters as device (device.id)}
			{@const u = usage[device.id]}
			<section class="rounded-card border border-line bg-surface/70 p-5 sm:p-6">
				<div class="flex items-start justify-between gap-3">
					<div class="flex items-center gap-3">
						<span
							class="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-accent-soft text-accent-ink"
						>
							<Icon name={device.device_type} size={22} />
						</span>
						<div class="min-w-0">
							<h3 class="truncate font-display text-lg font-semibold text-ink">{device.alias}</h3>
							<p class="truncate font-mono text-[11px] text-faint">{device.host}</p>
						</div>
					</div>
					<button
						type="button"
						onclick={() => loadOne(device.id)}
						disabled={loading[device.id]}
						aria-label="Refresh energy data"
						class="grid h-9 w-9 place-items-center rounded-full border border-line text-muted transition-colors hover:border-accent hover:text-accent-ink disabled:opacity-60"
					>
						<Icon name="refresh" size={15} class={loading[device.id] ? 'animate-spin' : ''} />
					</button>
				</div>

				{#if errors[device.id]}
					<p class="mt-5 text-sm text-muted">{errors[device.id]}</p>
				{:else if !u}
					<div class="mt-5 h-40 animate-pulse rounded-xl bg-raised/60"></div>
				{:else}
					<!-- live readouts -->
					<dl class="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
						{#each [{ k: 'Now', v: fmt(u.current_power_w, 1), unit: 'W', cost: null }, { k: 'Today', v: fmt(u.today_kwh), unit: 'kWh', cost: u.today_cost }, { k: 'This month', v: fmt(u.month_kwh), unit: 'kWh', cost: u.month_cost }, { k: 'Voltage', v: fmt(u.voltage, 0), unit: 'V', cost: null }] as s (s.k)}
							<div class="rounded-xl border border-line bg-raised/50 px-3 py-2.5">
								<dt class="text-[11px] uppercase tracking-wide text-faint">{s.k}</dt>
								<dd class="mt-0.5 font-display text-xl font-semibold text-ink">
									{s.v}<span class="ml-1 text-xs font-normal text-muted">{s.unit}</span>
								</dd>
								{#if energyRate != null && s.cost != null}
									<dd class="mt-0.5 font-mono text-xs text-muted">{fmtMoney(s.cost)}</dd>
								{/if}
							</div>
						{/each}
					</dl>

					<div class="mt-6 grid gap-6 lg:grid-cols-2">
						<div>
							<h4
								class="mb-3 font-display text-xs font-semibold uppercase tracking-[0.18em] text-muted"
							>
								This month · daily
							</h4>
							<EnergyChart data={u.daily} {currency} />
						</div>
						<div>
							<h4
								class="mb-3 font-display text-xs font-semibold uppercase tracking-[0.18em] text-muted"
							>
								This year · monthly
							</h4>
							<EnergyChart data={u.monthly} {currency} />
						</div>
					</div>
				{/if}
			</section>
		{/each}
	</div>
{/if}
