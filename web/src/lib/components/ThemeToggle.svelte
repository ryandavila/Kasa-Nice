<script lang="ts">
	import { theme } from '$lib/stores/theme.svelte';
	import Icon from './Icon.svelte';
</script>

<button
	type="button"
	onclick={() => theme.toggle()}
	aria-label="Toggle color theme"
	class="grid h-10 w-10 place-items-center rounded-full border border-line bg-surface text-muted transition-colors hover:border-accent hover:text-accent-ink"
>
	<Icon name={theme.current === 'light' ? 'moon' : 'sun'} size={18} />
</button>
