<script lang="ts">
	import { fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import { toasts } from '$lib/stores/toasts.svelte';
	import Icon from './Icon.svelte';
	import type { IconName } from './Icon.svelte';

	const ICON: Record<string, IconName> = {
		on: 'power',
		off: 'power',
		info: 'radar',
		error: 'bolt'
	};
</script>

<div class="pointer-events-none fixed bottom-5 right-5 z-50 flex flex-col gap-2">
	{#each toasts.items as toast (toast.id)}
		<div
			animate:flip={{ duration: 220 }}
			in:fly={{ y: 12, duration: 240 }}
			out:fly={{ y: 12, duration: 180 }}
			class="pointer-events-auto flex items-center gap-2.5 rounded-full border border-line bg-surface/95 py-2 pl-3 pr-4 text-sm shadow-lg backdrop-blur
				{toast.kind === 'error' ? 'text-red-500' : toast.kind === 'off' ? 'text-muted' : 'text-accent-ink'}"
		>
			<Icon name={ICON[toast.kind]} size={15} />
			<span class="text-ink">{toast.message}</span>
		</div>
	{/each}
</div>
