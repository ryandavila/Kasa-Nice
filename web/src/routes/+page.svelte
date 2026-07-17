<script lang="ts">
	import { onMount } from 'svelte';
	import type { DeviceType } from '$lib/api/types';
	import { deviceStore, TYPE_ORDER } from '$lib/stores/devices.svelte';
	import { groupStore } from '$lib/stores/groups.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import DeviceCard from '$lib/components/DeviceCard.svelte';
	import DiscoveryPanel from '$lib/components/DiscoveryPanel.svelte';
	import EnergyPanel from '$lib/components/EnergyPanel.svelte';
	import RoomsView from '$lib/components/RoomsView.svelte';
	import ScenesView from '$lib/components/ScenesView.svelte';
	import SchedulesView from '$lib/components/SchedulesView.svelte';
	import VacationSettings from '$lib/components/VacationSettings.svelte';
	import ThemeToggle from '$lib/components/ThemeToggle.svelte';
	import AlertsBell from '$lib/components/AlertsBell.svelte';
	import SettingsPanel from '$lib/components/SettingsPanel.svelte';
	import { vacationStore } from '$lib/stores/vacation.svelte';

	type Tab = 'devices' | 'energy' | 'scenes' | 'schedules' | 'vacation' | 'discovery';
	let tab = $state<Tab>('devices');

	// Load vacation status up front so the header indicator is correct without
	// opening its tab. Self-contained: the tab re-loads if opened first.
	vacationStore.load();

	// How the Devices tab groups cards: by device type (default) or by user room.
	// Persisted so the choice survives reloads.
	type Grouping = 'type' | 'room';
	let grouping = $state<Grouping>('type');

	function setGrouping(g: Grouping) {
		grouping = g;
		try {
			localStorage.setItem('kasa-grouping', g);
		} catch {
			// private mode / storage disabled — the choice just won't persist
		}
	}

	const LABELS: Record<DeviceType, string> = {
		Bulb: 'Bulbs',
		LightStrip: 'Light Strips',
		Dimmer: 'Dimmers',
		Strip: 'Power Strips',
		Plug: 'Plugs',
		Unknown: 'Other'
	};

	// TYPE_ORDER is unique and Unknown is appended once, so no dedupe is needed.
	const ordered: DeviceType[] = [...TYPE_ORDER, 'Unknown'];
	const groups = $derived(
		ordered
			.map((type) => ({
				type,
				label: LABELS[type],
				devices: deviceStore.devices.filter((d) => d.device_type === type)
			}))
			.filter((g) => g.devices.length > 0)
	);

	const loading = $derived(deviceStore.status === 'loading');

	onMount(() => {
		const saved = localStorage.getItem('kasa-grouping');
		if (saved === 'room' || saved === 'type') grouping = saved;

		deviceStore.load();
		deviceStore.startLiveUpdates();
		groupStore.load();

		// Drop the live stream while the tab is hidden; resume with an immediate
		// refresh so the UI is correct the moment it's visible again.
		const onVisibility = () => {
			if (document.hidden) {
				deviceStore.stopLiveUpdates();
			} else {
				deviceStore.refresh();
				deviceStore.startLiveUpdates();
			}
		};
		document.addEventListener('visibilitychange', onVisibility);

		return () => {
			document.removeEventListener('visibilitychange', onVisibility);
			deviceStore.stopLiveUpdates();
		};
	});
</script>

<svelte:head>
	<title>Kasa Nice — Smart Home Control</title>
</svelte:head>

<div class="mx-auto min-h-dvh max-w-5xl px-4 pb-24 sm:px-6">
	<!-- ── Header ─────────────────────────────────────────────────────────── -->
	<header class="flex items-center justify-between gap-4 py-6">
		<button type="button" onclick={() => (tab = 'devices')} class="flex items-center gap-2.5">
			<span
				class="grid h-9 w-9 place-items-center rounded-xl bg-accent text-[#04201f] shadow-[0_6px_18px_-6px_var(--glow)]"
			>
				<Icon name="bolt" size={18} stroke={2} />
			</span>
			<span class="font-display text-xl font-semibold tracking-tight text-ink">
				Kasa<span class="text-faint">Nice</span>
			</span>
		</button>

		<div class="flex items-center gap-2">
			{#if deviceStore.status === 'ready'}
				<span
					class="hidden items-center gap-1.5 px-1 text-xs font-medium text-faint sm:flex"
					title={deviceStore.live
						? 'Live — state updates automatically'
						: 'Reconnecting to the hub'}
				>
					<span class="relative grid h-2 w-2 place-items-center" aria-hidden="true">
						{#if deviceStore.live}
							<span class="absolute h-2 w-2 animate-ping rounded-full bg-accent opacity-60"></span>
						{/if}
						<span class="h-2 w-2 rounded-full {deviceStore.live ? 'bg-accent' : 'bg-faint'}"></span>
					</span>
					{deviceStore.live ? 'Live' : 'Offline'}
				</span>
			{/if}
			<button
				type="button"
				onclick={() => deviceStore.rediscover()}
				disabled={loading}
				class="flex h-10 items-center gap-2 rounded-full border border-line bg-surface px-4 text-sm font-medium text-muted transition-colors hover:border-accent hover:text-accent-ink disabled:opacity-60"
			>
				<Icon name="refresh" size={16} class={loading ? 'animate-spin' : ''} />
				<span class="hidden sm:inline">Rediscover</span>
			</button>
			<!-- Subtle vacation-mode indicator: only shown while the simulation is
			     actively running its window. Clicking jumps to its settings. -->
			{#if vacationStore.active}
				<button
					type="button"
					onclick={() => (tab = 'vacation')}
					title="Vacation mode is active — lights are being simulated"
					class="flex h-10 items-center gap-1.5 rounded-full border border-accent/40 bg-accent-soft px-3 text-sm font-medium text-accent-ink"
				>
					<Icon name="moon" size={15} />
					<span class="hidden sm:inline">Vacation</span>
				</button>
			{/if}
			<AlertsBell />
			<SettingsPanel />
			<ThemeToggle />
		</div>
	</header>

	<!-- ── Tabs ───────────────────────────────────────────────────────────── -->
	<nav class="mb-8 inline-flex rounded-full border border-line bg-surface/70 p-1">
		{#each [{ id: 'devices', label: 'Devices' }, { id: 'energy', label: 'Energy' }, { id: 'scenes', label: 'Scenes' }, { id: 'schedules', label: 'Schedules' }, { id: 'vacation', label: 'Vacation' }, { id: 'discovery', label: 'Discovery' }] as t (t.id)}
			<button
				type="button"
				onclick={() => (tab = t.id as Tab)}
				class="relative rounded-full px-5 py-2 text-sm font-medium transition-colors
					{tab === t.id ? 'text-[#04201f]' : 'text-muted hover:text-ink'}"
			>
				{#if tab === t.id}
					<span class="absolute inset-0 rounded-full bg-accent"></span>
				{/if}
				<span class="relative flex items-center gap-2">
					{t.label}
					{#if t.id === 'devices' && deviceStore.devices.length}
						<span
							class="rounded-full px-1.5 text-xs tabular-nums {tab === t.id
								? 'bg-black/10'
								: 'bg-raised text-faint'}">{deviceStore.devices.length}</span
						>
					{/if}
				</span>
			</button>
		{/each}
	</nav>

	<!-- ── Content ────────────────────────────────────────────────────────── -->
	{#if tab === 'devices'}
		{#if loading && !deviceStore.devices.length}
			<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
				{#each Array.from({ length: 4 }, (_, i) => i) as i (i)}
					<div class="h-36 animate-pulse rounded-card border border-line bg-surface/50"></div>
				{/each}
			</div>
		{:else if deviceStore.status === 'error'}
			<div class="rounded-card border border-line bg-surface/70 p-10 text-center">
				<p class="font-display text-lg text-ink">Couldn't reach the hub</p>
				<p class="mx-auto mt-1 max-w-sm text-sm text-muted">{deviceStore.error}</p>
				<button
					type="button"
					onclick={() => deviceStore.load()}
					class="mt-5 rounded-full bg-accent px-5 py-2.5 text-sm font-semibold text-[#04201f] hover:brightness-105"
				>
					Try again
				</button>
			</div>
		{:else if deviceStore.discovering && !deviceStore.devices.length}
			<div class="rounded-card border border-dashed border-line bg-surface/40 p-12 text-center">
				<span
					class="mx-auto grid h-14 w-14 animate-pulse place-items-center rounded-2xl bg-accent-soft text-accent-ink"
				>
					<Icon name="radar" size={26} />
				</span>
				<p class="mt-4 font-display text-lg text-ink">Scanning your network…</p>
				<p class="mx-auto mt-1 max-w-xs text-sm text-muted">
					Looking for Kasa devices. They'll appear here as they're found.
				</p>
			</div>
		{:else if deviceStore.isEmpty}
			<div class="rounded-card border border-dashed border-line bg-surface/40 p-12 text-center">
				<span
					class="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-accent-soft text-accent-ink"
				>
					<Icon name="radar" size={26} />
				</span>
				<p class="mt-4 font-display text-lg text-ink">No devices yet</p>
				<p class="mx-auto mt-1 max-w-xs text-sm text-muted">
					Make sure your Kasa devices are powered on and on this network, then scan.
				</p>
				<button
					type="button"
					onclick={() => deviceStore.rediscover()}
					class="mt-5 rounded-full bg-accent px-5 py-2.5 text-sm font-semibold text-[#04201f] hover:brightness-105"
				>
					Scan network
				</button>
			</div>
		{:else}
			{#if deviceStore.discovering}
				<div
					class="mb-5 flex items-center gap-2.5 rounded-card border border-line bg-surface/60 px-4 py-2.5 text-sm text-muted"
				>
					<Icon name="refresh" size={15} class="animate-spin" />
					Still scanning the network — more devices may appear.
				</div>
			{/if}

			<!-- Group cards by device type or by user-defined room. -->
			<div class="mb-6 inline-flex rounded-full border border-line bg-surface/70 p-1">
				{#each [{ id: 'type', label: 'By type' }, { id: 'room', label: 'By room' }] as g (g.id)}
					<button
						type="button"
						onclick={() => setGrouping(g.id as Grouping)}
						class="relative rounded-full px-4 py-1.5 text-xs font-medium transition-colors
							{grouping === g.id ? 'text-[#04201f]' : 'text-muted hover:text-ink'}"
					>
						{#if grouping === g.id}
							<span class="absolute inset-0 rounded-full bg-accent"></span>
						{/if}
						<span class="relative">{g.label}</span>
					</button>
				{/each}
			</div>

			{#if grouping === 'room'}
				<RoomsView />
			{:else}
				<div class="space-y-10">
					{#each groups as group (group.type)}
						<section>
							<div class="mb-4 flex items-baseline gap-3">
								<h2
									class="font-display text-sm font-semibold tracking-[0.18em] text-muted uppercase"
								>
									{group.label}
								</h2>
								<span class="h-px grow bg-line"></span>
								<span class="font-mono text-xs text-faint">{group.devices.length}</span>
							</div>
							<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
								{#each group.devices as device, i (device.id)}
									<DeviceCard {device} delay={i * 45} />
								{/each}
							</div>
						</section>
					{/each}
				</div>
			{/if}
		{/if}
	{:else if tab === 'energy'}
		<EnergyPanel />
	{:else if tab === 'scenes'}
		<ScenesView />
	{:else if tab === 'schedules'}
		<SchedulesView />
	{:else if tab === 'vacation'}
		<VacationSettings />
	{:else}
		<DiscoveryPanel />
	{/if}
</div>
