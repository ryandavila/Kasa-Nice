<script lang="ts">
	import { onMount } from 'svelte';
	import type { DeviceType } from '$lib/api/types';
	import { deviceStore, TYPE_ORDER } from '$lib/stores/devices.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import DeviceCard from '$lib/components/DeviceCard.svelte';
	import DiscoveryPanel from '$lib/components/DiscoveryPanel.svelte';
	import ThemeToggle from '$lib/components/ThemeToggle.svelte';

	type Tab = 'devices' | 'discovery';
	let tab = $state<Tab>('devices');

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
		deviceStore.load();
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
			<button
				type="button"
				onclick={() => deviceStore.rediscover()}
				disabled={loading}
				class="flex h-10 items-center gap-2 rounded-full border border-line bg-surface px-4 text-sm font-medium text-muted transition-colors hover:border-accent hover:text-accent-ink disabled:opacity-60"
			>
				<Icon name="refresh" size={16} class={loading ? 'animate-spin' : ''} />
				<span class="hidden sm:inline">Rediscover</span>
			</button>
			<ThemeToggle />
		</div>
	</header>

	<!-- ── Tabs ───────────────────────────────────────────────────────────── -->
	<nav class="mb-8 inline-flex rounded-full border border-line bg-surface/70 p-1">
		{#each [{ id: 'devices', label: 'Devices' }, { id: 'discovery', label: 'Discovery' }] as t (t.id)}
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
			<div class="space-y-10">
				{#each groups as group (group.type)}
					<section>
						<div class="mb-4 flex items-baseline gap-3">
							<h2 class="font-display text-sm font-semibold uppercase tracking-[0.18em] text-muted">
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
	{:else}
		<DiscoveryPanel />
	{/if}
</div>
