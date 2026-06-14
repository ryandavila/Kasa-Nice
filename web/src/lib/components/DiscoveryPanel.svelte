<script lang="ts">
	import { onMount } from 'svelte';
	import type { Device } from '$lib/api/types';
	import { deviceStore } from '$lib/stores/devices.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { ApiError, getConfig } from '$lib/api/client';
	import Icon from './Icon.svelte';
	import DeviceCard from './DeviceCard.svelte';

	let subnet = $state('');
	let target = $state('');
	let scanning = $state(false);
	let probing = $state(false);
	let found = $state<Device[]>([]);
	let searched = $state(false);

	onMount(async () => {
		try {
			const cfg = await getConfig();
			if (cfg.scan_subnet) subnet = cfg.scan_subnet;
		} catch {
			// config is best-effort; the field just starts empty
		}
	});

	function report(devices: Device[]) {
		found = devices;
		searched = true;
		toasts.push(
			devices.length
				? `Found ${devices.length} device${devices.length > 1 ? 's' : ''}`
				: 'No devices found',
			devices.length ? 'info' : 'off'
		);
	}

	function fail(e: unknown, fallback: string) {
		toasts.push(e instanceof ApiError ? e.message : fallback, 'error');
		found = [];
		searched = true;
	}

	async function scan() {
		if (!subnet.trim() || scanning) return;
		scanning = true;
		try {
			report(await deviceStore.scanSubnet(subnet.trim()));
		} catch (e) {
			fail(e, 'Subnet scan failed');
		} finally {
			scanning = false;
		}
	}

	async function probe() {
		if (!target.trim() || probing) return;
		probing = true;
		try {
			report(await deviceStore.discoverTarget(target.trim()));
		} catch (e) {
			fail(e, 'Discovery failed');
		} finally {
			probing = false;
		}
	}

	const busy = $derived(scanning || probing);
</script>

<section class="mx-auto max-w-2xl">
	<div class="rounded-card border border-line bg-surface/70 p-6 sm:p-8">
		<div class="flex items-center gap-3">
			<span class="grid h-11 w-11 place-items-center rounded-2xl bg-accent-soft text-accent-ink">
				<Icon name="radar" size={22} />
			</span>
			<div>
				<h2 class="font-display text-xl font-semibold text-ink">Find devices</h2>
				<p class="text-sm text-muted">Sweep a subnet, or probe a single address.</p>
			</div>
		</div>

		<!-- Subnet sweep: the reliable path when devices live on another VLAN -->
		<form
			class="mt-6 flex flex-col gap-2 sm:flex-row"
			onsubmit={(e) => {
				e.preventDefault();
				scan();
			}}
		>
			<div
				class="flex grow items-center gap-2 rounded-xl border border-line bg-raised px-3 focus-within:border-accent"
			>
				<Icon name="radar" size={16} class="text-faint" />
				<input
					bind:value={subnet}
					placeholder="192.168.1.0/24"
					aria-label="Subnet in CIDR notation"
					class="w-full bg-transparent py-3 font-mono text-sm text-ink outline-none placeholder:text-faint"
				/>
			</div>
			<button
				type="submit"
				disabled={busy || !subnet.trim()}
				class="flex items-center justify-center gap-2 rounded-xl bg-accent px-5 py-3 text-sm font-semibold text-[#04201f] transition hover:brightness-105 disabled:opacity-60"
			>
				<Icon
					name={scanning ? 'refresh' : 'radar'}
					size={16}
					class={scanning ? 'animate-spin' : ''}
				/>
				{scanning ? 'Scanning…' : 'Scan subnet'}
			</button>
		</form>
		<p class="mt-3 text-xs leading-relaxed text-faint">
			Broadcast discovery can't cross VLAN boundaries, so this probes every address in the subnet
			directly. Slower, but it reaches devices on an isolated network.
		</p>

		<div class="my-6 flex items-center gap-3 text-faint">
			<span class="h-px grow bg-line"></span>
			<span class="text-xs font-medium uppercase tracking-wider">or a single host</span>
			<span class="h-px grow bg-line"></span>
		</div>

		<!-- Single IP probe -->
		<form
			class="flex flex-col gap-2 sm:flex-row"
			onsubmit={(e) => {
				e.preventDefault();
				probe();
			}}
		>
			<div
				class="flex grow items-center gap-2 rounded-xl border border-line bg-raised px-3 focus-within:border-accent"
			>
				<Icon name="search" size={16} class="text-faint" />
				<input
					bind:value={target}
					inputmode="decimal"
					placeholder="192.168.1.24"
					aria-label="Device IP address"
					class="w-full bg-transparent py-3 font-mono text-sm text-ink outline-none placeholder:text-faint"
				/>
			</div>
			<button
				type="submit"
				disabled={busy || !target.trim()}
				class="flex items-center justify-center gap-2 rounded-xl border border-line px-5 py-3 text-sm font-semibold text-ink transition hover:border-accent disabled:opacity-60"
			>
				<Icon
					name={probing ? 'refresh' : 'search'}
					size={16}
					class={probing ? 'animate-spin' : ''}
				/>
				{probing ? 'Probing…' : 'Probe IP'}
			</button>
		</form>
	</div>

	{#if searched && !busy}
		<div class="mt-6">
			{#if found.length}
				<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
					{#each found as device, i (device.id)}
						<DeviceCard {device} delay={i * 50} />
					{/each}
				</div>
			{:else}
				<p class="py-8 text-center text-sm text-muted">No devices answered.</p>
			{/if}
		</div>
	{/if}
</section>
