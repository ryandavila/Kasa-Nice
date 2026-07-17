<script lang="ts">
	import type { Device } from '$lib/api/types';
	import { deviceStore } from '$lib/stores/devices.svelte';
	import Icon from './Icon.svelte';

	// A known device that didn't answer discovery. Rendered from its last-known
	// snapshot (or host-only identity) as a grayed, NON-interactive card so it
	// doesn't vanish from its room/type group while occupying rooms and favorites.
	// The only action is a retry probe; all live controls are intentionally absent.
	let { device, delay = 0 }: { device: Device; delay?: number } = $props();

	const retrying = $derived(deviceStore.retrying[device.id] ?? false);
	const meta = $derived([device.model, device.host].filter(Boolean).join('  ·  '));
</script>

<article
	class="animate-rise relative overflow-hidden rounded-card border border-dashed border-line bg-surface/40 p-5 opacity-75 transition-all duration-500"
	style="animation-delay: {delay}ms;"
>
	<header class="flex items-start gap-4">
		<div
			class="grid h-12 w-12 shrink-0 place-items-center rounded-2xl border border-line bg-raised text-faint"
		>
			<Icon name={device.device_type} size={24} />
		</div>

		<div class="min-w-0 grow">
			<h3 class="truncate font-display text-lg leading-tight font-semibold text-muted">
				{device.alias}
			</h3>
			<p class="mt-0.5 truncate font-mono text-[11px] tracking-tight text-faint">{meta}</p>
			<p class="mt-1 inline-flex items-center gap-1.5 text-xs font-medium text-faint">
				<Icon name="unreachable" size={13} />
				Unreachable
			</p>
		</div>

		<button
			type="button"
			onclick={() => deviceStore.retryDevice(device)}
			disabled={retrying}
			class="flex h-8 shrink-0 items-center gap-1.5 rounded-full border border-line bg-surface px-3 text-xs font-medium text-muted transition-colors hover:border-accent hover:text-accent-ink disabled:opacity-60"
		>
			<Icon name="refresh" size={14} class={retrying ? 'animate-spin' : ''} />
			{retrying ? 'Retrying' : 'Retry'}
		</button>
	</header>
</article>
