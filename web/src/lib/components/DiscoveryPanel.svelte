<script lang="ts">
	import type { Device } from '$lib/api/types';
	import { deviceStore } from '$lib/stores/devices.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { ApiError } from '$lib/api/client';
	import Icon from './Icon.svelte';
	import DeviceCard from './DeviceCard.svelte';

	let target = $state('255.255.255.255');
	let loading = $state(false);
	let found = $state<Device[]>([]);
	let searched = $state(false);

	async function discover() {
		if (!target.trim() || loading) return;
		loading = true;
		searched = true;
		try {
			found = await deviceStore.discoverTarget(target.trim());
			toasts.push(
				found.length
					? `Found ${found.length} device${found.length > 1 ? 's' : ''}`
					: 'No devices found',
				found.length ? 'info' : 'off'
			);
		} catch (e) {
			toasts.push(e instanceof ApiError ? e.message : 'Discovery failed', 'error');
			found = [];
		} finally {
			loading = false;
		}
	}
</script>

<section class="mx-auto max-w-2xl">
	<div class="rounded-card border border-line bg-surface/70 p-6 sm:p-8">
		<div class="flex items-center gap-3">
			<span class="grid h-11 w-11 place-items-center rounded-2xl bg-accent-soft text-accent-ink">
				<Icon name="radar" size={22} />
			</span>
			<div>
				<h2 class="font-display text-xl font-semibold text-ink">Find a device</h2>
				<p class="text-sm text-muted">Probe your LAN when something isn't showing up.</p>
			</div>
		</div>

		<form
			class="mt-6 flex flex-col gap-2 sm:flex-row"
			onsubmit={(e) => {
				e.preventDefault();
				discover();
			}}
		>
			<div
				class="flex grow items-center gap-2 rounded-xl border border-line bg-raised px-3 focus-within:border-accent"
			>
				<Icon name="search" size={16} class="text-faint" />
				<input
					bind:value={target}
					inputmode="decimal"
					placeholder="255.255.255.255"
					aria-label="LAN IP address"
					class="w-full bg-transparent py-3 font-mono text-sm text-ink outline-none placeholder:text-faint"
				/>
			</div>
			<button
				type="submit"
				disabled={loading}
				class="flex items-center justify-center gap-2 rounded-xl bg-accent px-5 py-3 text-sm font-semibold text-[#04201f] transition hover:brightness-105 disabled:opacity-60"
			>
				<Icon
					name={loading ? 'refresh' : 'radar'}
					size={16}
					class={loading ? 'animate-spin' : ''}
				/>
				{loading ? 'Scanning…' : 'Discover'}
			</button>
		</form>

		<p class="mt-3 text-xs leading-relaxed text-faint">
			Enter a device's IP to probe it directly, or use the broadcast address
			<span class="font-mono">255.255.255.255</span> to sweep the whole network. Found devices are added
			to your Devices tab.
		</p>
	</div>

	{#if searched && !loading}
		<div class="mt-6">
			{#if found.length}
				<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
					{#each found as device, i (device.id)}
						<DeviceCard {device} delay={i * 50} />
					{/each}
				</div>
			{:else}
				<p class="py-8 text-center text-sm text-muted">No devices answered at that address.</p>
			{/if}
		</div>
	{/if}
</section>
