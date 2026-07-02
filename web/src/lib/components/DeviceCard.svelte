<script lang="ts">
	import type { ChildPlug, Device } from '$lib/api/types';
	import { deviceStore } from '$lib/stores/devices.svelte';
	import { groupStore } from '$lib/stores/groups.svelte';
	import Icon from './Icon.svelte';
	import Toggle from './Toggle.svelte';
	import BrightnessSlider from './BrightnessSlider.svelte';
	import ColorControl from './ColorControl.svelte';

	let {
		device,
		delay = 0,
		showRoom = false
	}: { device: Device; delay?: number; showRoom?: boolean } = $props();

	const isStrip = $derived(device.children.length >= 2);
	const busy = $derived(deviceStore.busy[device.id] ?? false);
	const onCount = $derived(device.children.filter((c) => c.is_on).length);
	const live = $derived(isStrip ? onCount > 0 : device.is_on);
	const meta = $derived([device.model, device.host].filter(Boolean).join('  ·  '));

	const fav = $derived(groupStore.isFavorite(device.id));
	const roomId = $derived(groupStore.groupOf(device.id)?.id ?? '');

	// ── Inline rename ──────────────────────────────────────────────────────────
	// Enter or blur saves; Escape cancels. Cancelling resets the draft to the
	// current name first, so the blur that fires as the input unmounts is a no-op.
	let editingTitle = $state(false);
	let titleDraft = $state('');
	// The stable id of the outlet being renamed, or null when none is.
	let editingChildId = $state<string | null>(null);
	let childDraft = $state('');

	function startTitleEdit() {
		titleDraft = device.alias;
		editingTitle = true;
	}
	function commitTitle() {
		const name = titleDraft.trim();
		if (name && name !== device.alias) deviceStore.renameDevice(device, name);
		editingTitle = false;
	}
	function cancelTitle() {
		titleDraft = device.alias; // makes the unmount-blur commit a no-op
		editingTitle = false;
	}

	function startChildEdit(child: ChildPlug) {
		childDraft = child.alias;
		editingChildId = child.id;
	}
	function commitChild(child: ChildPlug) {
		const name = childDraft.trim();
		if (name && name !== child.alias) deviceStore.renameChild(device, child.id, name);
		editingChildId = null;
	}
	function cancelChild(child: ChildPlug) {
		childDraft = child.alias;
		editingChildId = null;
	}

	/** Focus (and select) an inline-rename input the moment it appears. */
	function focusSelect(node: HTMLInputElement) {
		node.focus();
		node.select();
	}
</script>

<article
	class="animate-rise group relative overflow-hidden rounded-card border p-5 transition-all duration-500
		{live ? 'border-accent/40 bg-surface' : 'border-line bg-surface/70'}"
	style="animation-delay: {delay}ms; {live
		? 'box-shadow: 0 1px 0 0 var(--accent-soft), 0 18px 40px -28px var(--glow);'
		: ''}"
	class:opacity-70={busy}
>
	<!-- energized wash -->
	<div
		class="pointer-events-none absolute -right-10 -top-14 h-32 w-32 rounded-full blur-2xl transition-opacity duration-500"
		style="background: var(--glow); opacity: {live ? 0.18 : 0};"
	></div>

	<header class="flex items-start gap-4">
		<div
			class="grid h-12 w-12 shrink-0 place-items-center rounded-2xl border transition-colors duration-500
				{live ? 'border-accent/30 bg-accent-soft text-accent-ink' : 'border-line bg-raised text-faint'}"
		>
			<Icon name={device.device_type} size={24} />
		</div>

		<div class="min-w-0 grow">
			{#if editingTitle}
				<input
					use:focusSelect
					bind:value={titleDraft}
					aria-label="Rename {device.alias}"
					maxlength="60"
					onblur={commitTitle}
					onkeydown={(e) => {
						if (e.key === 'Enter') {
							e.preventDefault();
							commitTitle();
						} else if (e.key === 'Escape') {
							e.preventDefault();
							cancelTitle();
						}
					}}
					class="w-full rounded-lg border border-accent/50 bg-raised px-2 py-0.5 font-display text-lg font-semibold leading-tight text-ink outline-none focus:border-accent"
				/>
			{:else}
				<h3
					class="flex items-center gap-1.5 font-display text-lg font-semibold leading-tight text-ink"
				>
					<span class="truncate">{device.alias}</span>
					{#if device.can_rename}
						<button
							type="button"
							onclick={startTitleEdit}
							aria-label="Rename {device.alias}"
							title="Rename"
							class="grid h-6 w-6 shrink-0 place-items-center rounded-md text-faint opacity-0 transition-opacity hover:text-muted focus:opacity-100 group-hover:opacity-100"
						>
							<Icon name="pencil" size={14} />
						</button>
					{/if}
				</h3>
			{/if}
			<p class="mt-0.5 truncate font-mono text-[11px] tracking-tight text-faint">{meta}</p>
			{#if isStrip}
				<p class="mt-1 text-xs font-medium text-muted">
					{onCount} of {device.children.length} outlets on
				</p>
			{/if}
		</div>

		<div class="flex shrink-0 items-center gap-2">
			<button
				type="button"
				onclick={() => groupStore.toggleFavorite(device.id)}
				aria-pressed={fav}
				aria-label={fav ? 'Remove from favorites' : 'Add to favorites'}
				title={fav ? 'Favorited' : 'Add to favorites'}
				class="grid h-8 w-8 place-items-center rounded-full transition-colors
					{fav ? 'text-accent-ink' : 'text-faint hover:text-muted'}"
			>
				<Icon name={fav ? 'star-filled' : 'star'} size={18} />
			</button>
			{#if !isStrip}
				<Toggle
					checked={device.is_on}
					disabled={busy}
					label="Toggle {device.alias}"
					onchange={(on) => deviceStore.togglePower(device, on)}
				/>
			{/if}
		</div>
	</header>

	{#if !isStrip && (device.is_dimmable || device.is_color)}
		<div class="mt-5 space-y-4">
			{#if device.is_dimmable}
				<BrightnessSlider
					value={device.brightness ?? 0}
					disabled={busy}
					onchange={(v) => deviceStore.setBrightness(device, v)}
				/>
			{/if}
			{#if device.is_color}
				<ColorControl
					hsv={device.hsv}
					disabled={busy}
					onchange={(hex) => deviceStore.setColor(device, hex)}
				/>
			{/if}
		</div>
	{/if}

	{#if isStrip}
		<ul class="mt-4 divide-y divide-line border-t border-line">
			{#each device.children as child (child.id)}
				<li class="group/outlet flex items-center justify-between gap-3 py-2.5">
					{#if editingChildId === child.id}
						<input
							use:focusSelect
							bind:value={childDraft}
							aria-label="Rename {child.alias}"
							maxlength="60"
							onblur={() => commitChild(child)}
							onkeydown={(e) => {
								if (e.key === 'Enter') {
									e.preventDefault();
									commitChild(child);
								} else if (e.key === 'Escape') {
									e.preventDefault();
									cancelChild(child);
								}
							}}
							class="min-w-0 grow rounded-md border border-accent/50 bg-raised px-2 py-0.5 text-sm text-ink outline-none focus:border-accent"
						/>
					{:else}
						<span class="flex min-w-0 items-center gap-2 text-sm text-ink">
							<Icon name="bolt" size={14} class={child.is_on ? 'text-accent-ink' : 'text-faint'} />
							<span class="truncate">{child.alias}</span>
							{#if device.can_rename}
								<button
									type="button"
									onclick={() => startChildEdit(child)}
									aria-label="Rename {child.alias}"
									title="Rename"
									class="grid h-6 w-6 shrink-0 place-items-center rounded-md text-faint opacity-0 transition-opacity hover:text-muted focus:opacity-100 group-hover/outlet:opacity-100"
								>
									<Icon name="pencil" size={12} />
								</button>
							{/if}
						</span>
					{/if}
					<Toggle
						size="sm"
						checked={child.is_on}
						disabled={busy}
						label="Toggle {child.alias}"
						onchange={(on) => deviceStore.toggleChild(device, child.id, on)}
					/>
				</li>
			{/each}
		</ul>
	{/if}

	{#if showRoom && groupStore.groups.length}
		<div class="mt-4 flex items-center gap-2 border-t border-line pt-3">
			<Icon name="home" size={14} class="shrink-0 text-faint" />
			<label class="sr-only" for="room-{device.id}">Room for {device.alias}</label>
			<select
				id="room-{device.id}"
				value={roomId}
				onchange={(e) => groupStore.assignDevice(device.id, e.currentTarget.value || null)}
				class="w-full rounded-lg border border-line bg-raised/50 px-2 py-1.5 text-xs text-muted transition-colors hover:border-accent/50 focus:text-ink"
			>
				<option value="">No room</option>
				{#each groupStore.groups as room (room.id)}
					<option value={room.id}>{room.name}</option>
				{/each}
			</select>
		</div>
	{/if}
</article>
