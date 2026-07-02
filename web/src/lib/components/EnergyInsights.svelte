<script lang="ts">
	import { onMount } from 'svelte';
	import type { EnergyInsights } from '$lib/api/types';
	import { getEnergyInsights } from '$lib/api/client';
	import Icon from './Icon.svelte';

	// Cost display mirrors the parent panel: the flat rate (null hides money) and
	// the currency prefix are owned there and passed down so both stay in sync.
	let { energyRate, currency = '$' }: { energyRate: number | null; currency?: string } = $props();

	// Derived insights across every recorded device; null until first loaded, and
	// left null on failure so the section simply doesn't render (best-effort, like
	// the whole-home summary above it).
	let insights = $state<EnergyInsights | null>(null);

	onMount(async () => {
		try {
			insights = await getEnergyInsights();
		} catch {
			// insights are supplementary; the rest of the Energy tab still renders
		}
	});

	function fmt(v: number | null, digits = 2): string {
		return v == null ? '—' : v.toFixed(digits);
	}

	function fmtMoney(v: number | null): string {
		return v == null ? '—' : currency + v.toFixed(2);
	}

	// Week-over-week change vs last week. Null when last week has no data (a
	// percentage off a zero baseline is meaningless), so the UI shows a plain total.
	const weekDelta = $derived.by(() => {
		if (!insights) return null;
		const { this_week_kwh, last_week_kwh } = insights.week;
		const diff = this_week_kwh - last_week_kwh;
		const pct = last_week_kwh > 0 ? (diff / last_week_kwh) * 100 : null;
		return { diff, pct };
	});

	// Only devices whose overnight draw crossed the vampire-load threshold; the
	// server sorts by draw descending, so the worst offenders come first.
	const hogs = $derived(insights ? insights.idle.filter((d) => d.is_idle_hog) : []);
</script>

{#if insights}
	<section class="rounded-card border border-line bg-surface/70 p-5 sm:p-6">
		<div class="flex items-center gap-3">
			<span
				class="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-accent-soft text-accent-ink"
			>
				<Icon name="chart" size={22} />
			</span>
			<div class="min-w-0">
				<h3 class="truncate font-display text-lg font-semibold text-ink">Insights</h3>
				<p class="truncate text-[11px] text-faint">Trends across your recorded history</p>
			</div>
		</div>

		<!-- Month-end projection -->
		<div class="mt-5">
			<h4 class="mb-3 font-display text-xs font-semibold uppercase tracking-[0.18em] text-muted">
				Month-end projection
			</h4>
			<dl class="grid grid-cols-1 gap-3 sm:grid-cols-2">
				{#each [{ k: 'So far this month', v: insights.projection.month_to_date_kwh, cost: insights.projection.month_to_date_cost }, { k: 'Projected total', v: insights.projection.projected_kwh, cost: insights.projection.projected_cost }] as s (s.k)}
					<div class="rounded-xl border border-line bg-raised/50 px-3 py-2.5">
						<dt class="text-[11px] uppercase tracking-wide text-faint">{s.k}</dt>
						<dd class="mt-0.5 font-display text-xl font-semibold text-ink">
							{fmt(s.v)}<span class="ml-1 text-xs font-normal text-muted">kWh</span>
						</dd>
						{#if energyRate != null && s.cost != null}
							<dd class="mt-1 font-display text-base font-semibold text-accent-ink">
								≈{fmtMoney(s.cost)}
							</dd>
						{/if}
					</div>
				{/each}
			</dl>
			<p class="mt-2 text-[11px] text-faint">
				A linear estimate from this month's daily average — not a forecast of your bill.
			</p>
		</div>

		<!-- Week over week -->
		<div class="mt-6 border-t border-line pt-6">
			<h4 class="mb-3 font-display text-xs font-semibold uppercase tracking-[0.18em] text-muted">
				This week vs last
			</h4>
			<div class="flex flex-wrap items-baseline gap-x-6 gap-y-1">
				<div>
					<span class="font-display text-xl font-semibold text-ink">
						{fmt(insights.week.this_week_kwh)}
					</span>
					<span class="ml-1 text-xs text-muted">kWh this week</span>
				</div>
				<div class="text-sm text-muted">
					{fmt(insights.week.last_week_kwh)} kWh last week
				</div>
				{#if weekDelta}
					{@const up = weekDelta.diff >= 0}
					<span
						class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold {up
							? 'bg-red-500/10 text-red-500'
							: 'bg-accent-soft text-accent-ink'}"
					>
						{up ? '▲' : '▼'}
						{#if weekDelta.pct != null}
							{Math.abs(weekDelta.pct).toFixed(0)}%
						{:else}
							{fmt(Math.abs(weekDelta.diff))} kWh
						{/if}
					</span>
				{/if}
			</div>
		</div>

		<!-- Per-room rollups -->
		{#if insights.rooms.length}
			<div class="mt-6 border-t border-line pt-6">
				<h4 class="mb-3 font-display text-xs font-semibold uppercase tracking-[0.18em] text-muted">
					By room
				</h4>
				<div class="space-y-2">
					{#each insights.rooms as room (room.group_id)}
						<div
							class="flex items-center justify-between gap-3 rounded-xl border border-line bg-raised/50 px-3 py-2.5"
						>
							<span class="min-w-0 truncate font-display text-sm font-semibold text-ink">
								{room.name}
							</span>
							<div class="flex shrink-0 items-baseline gap-4 text-right">
								<div>
									<span class="font-display text-base font-semibold text-ink">
										{fmt(room.today_kwh)}
									</span>
									<span class="ml-0.5 text-[11px] text-faint">kWh today</span>
								</div>
								<div>
									<span class="font-display text-base font-semibold text-ink">
										{fmt(room.month_kwh)}
									</span>
									<span class="ml-0.5 text-[11px] text-faint">kWh month</span>
									{#if energyRate != null && room.month_cost != null}
										<span class="ml-1 text-xs font-semibold text-accent-ink">
											≈{fmtMoney(room.month_cost)}
										</span>
									{/if}
								</div>
							</div>
						</div>
					{/each}
				</div>
			</div>
		{/if}

		<!-- Idle (standby) draw -->
		{#if insights.idle.length}
			<div class="mt-6 border-t border-line pt-6">
				<div class="mb-3 flex items-center justify-between gap-2">
					<h4 class="font-display text-xs font-semibold uppercase tracking-[0.18em] text-muted">
						Overnight idle draw
					</h4>
					{#if hogs.length}
						<span class="text-[11px] font-semibold text-red-500">
							{hogs.length} always-on {hogs.length === 1 ? 'device' : 'devices'}
						</span>
					{/if}
				</div>
				<div class="space-y-2">
					{#each insights.idle as device (device.device_id)}
						<div
							class="flex items-center justify-between gap-3 rounded-xl border px-3 py-2.5 {device.is_idle_hog
								? 'border-red-500/40 bg-red-500/5'
								: 'border-line bg-raised/50'}"
						>
							<span class="min-w-0 truncate font-display text-sm font-semibold text-ink">
								{device.alias}
							</span>
							<div class="flex shrink-0 items-center gap-2">
								<span class="font-display text-base font-semibold text-ink">
									{fmt(device.idle_w, 1)}<span class="ml-0.5 text-[11px] text-faint">W</span>
								</span>
								{#if device.is_idle_hog}
									<span
										class="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-[11px] font-semibold text-red-500"
									>
										<Icon name="bolt" size={11} />
										Vampire
									</span>
								{/if}
							</div>
						</div>
					{/each}
				</div>
				<p class="mt-2 text-[11px] text-faint">
					Median draw between 1–5am over the last 14 days — a high value means a device burns power
					while idle.
				</p>
			</div>
		{/if}
	</section>
{/if}
