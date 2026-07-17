<script lang="ts">
	import { onMount } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import { deviceStore } from '$lib/stores/devices.svelte';
	import { sceneStore } from '$lib/stores/scenes.svelte';
	import Icon from './Icon.svelte';

	onMount(() => {
		sceneStore.load();
		// The create flow picks from the device list; the page loads it, but load
		// here too so the tab is self-sufficient if opened first.
		if (!deviceStore.devices.length) deviceStore.load();
	});

	// Composer state: whether it's open, the name draft, and the picked device ids.
	// A SvelteSet so mutating it (add/delete) is reactive without reassigning.
	let creating = $state(false);
	let name = $state('');
	const picked = new SvelteSet<string>();

	// Only reachable devices have live state worth capturing.
	const selectable = $derived(deviceStore.devices.filter((d) => d.reachable));
	const canSave = $derived(name.trim() !== '' && picked.size > 0);

	function openCreate() {
		name = '';
		picked.clear();
		creating = true;
	}

	function togglePick(id: string) {
		if (picked.has(id)) picked.delete(id);
		else picked.add(id);
	}

	async function save() {
		if (!canSave) return;
		const scene = await sceneStore.createFromDevices(name.trim(), [...picked]);
		if (scene) creating = false;
	}

	// Inline rename: which scene is being edited, and its draft name.
	let editingId = $state<string | null>(null);
	let draft = $state('');

	function startRename(id: string, current: string) {
		editingId = id;
		draft = current;
	}

	async function commitRename(id: string) {
		const next = draft.trim();
		if (next && next !== sceneStore.scenes.find((s) => s.id === id)?.name) {
			await sceneStore.rename(id, next);
		}
		editingId = null;
	}

	function deviceCount(n: number): string {
		return `${n} ${n === 1 ? 'device' : 'devices'}`;
	}
</script>

<div class="space-y-6">
	<!-- ── Toolbar ────────────────────────────────────────────────────────── -->
	<div class="flex items-center justify-between gap-3">
		<p class="text-sm text-muted">
			{sceneStore.scenes.length}
			{sceneStore.scenes.length === 1 ? 'scene' : 'scenes'}
		</p>
		{#if !creating}
			<button
				type="button"
				onclick={openCreate}
				class="flex h-9 items-center gap-2 rounded-full border border-line bg-surface px-4 text-sm font-medium text-muted transition-colors hover:border-accent hover:text-accent-ink"
			>
				<Icon name="plus" size={16} />
				New scene
			</button>
		{/if}
	</div>

	<!-- ── Composer ───────────────────────────────────────────────────────── -->
	{#if creating}
		<form
			class="space-y-5 rounded-card border border-accent/40 bg-surface/70 p-5"
			onsubmit={(e) => (e.preventDefault(), save())}
		>
			<label class="flex flex-col gap-1.5">
				<span class="text-xs font-medium tracking-wide text-faint uppercase">Scene name</span>
				<!-- svelte-ignore a11y_autofocus -->
				<input
					bind:value={name}
					autofocus
					placeholder="e.g. Movie night"
					class="h-10 rounded-lg border border-line bg-surface px-3 text-ink outline-none focus:border-accent"
				/>
			</label>

			<div class="flex flex-col gap-1.5">
				<span class="text-xs font-medium tracking-wide text-faint uppercase">
					Capture these devices' current state
				</span>
				{#if selectable.length}
					<div class="flex flex-wrap gap-1.5">
						{#each selectable as device (device.id)}
							<button
								type="button"
								onclick={() => togglePick(device.id)}
								aria-pressed={picked.has(device.id)}
								class="flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm font-medium transition-colors {picked.has(
									device.id
								)
									? 'border-accent bg-accent text-[#04201f]'
									: 'border-line bg-surface text-muted hover:text-ink'}"
							>
								{#if picked.has(device.id)}
									<Icon name="star-filled" size={14} />
								{/if}
								{device.alias}
							</button>
						{/each}
					</div>
				{:else}
					<p class="text-sm text-muted">No reachable devices to capture yet.</p>
				{/if}
			</div>

			<div class="flex items-center gap-2">
				<button
					type="submit"
					disabled={!canSave}
					class="h-9 rounded-full bg-accent px-5 text-sm font-semibold text-[#04201f] hover:brightness-105 disabled:opacity-40"
				>
					Save scene
				</button>
				<button
					type="button"
					onclick={() => (creating = false)}
					class="flex h-9 items-center gap-1.5 rounded-full border border-line px-4 text-sm font-medium text-muted hover:text-ink"
				>
					Cancel
				</button>
			</div>
		</form>
	{/if}

	<!-- ── Scene list ─────────────────────────────────────────────────────── -->
	{#if !sceneStore.scenes.length && !creating}
		<div class="rounded-card border border-dashed border-line bg-surface/40 p-12 text-center">
			<span
				class="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-accent-soft text-accent-ink"
			>
				<Icon name="bolt" size={26} />
			</span>
			<p class="mt-4 font-display text-lg text-ink">No scenes yet</p>
			<p class="mx-auto mt-1 max-w-xs text-sm text-muted">
				Set your devices how you like them, then save that as a scene to apply it any time in one
				tap.
			</p>
			<button
				type="button"
				onclick={openCreate}
				class="mt-5 rounded-full bg-accent px-5 py-2.5 text-sm font-semibold text-[#04201f] hover:brightness-105"
			>
				New scene
			</button>
		</div>
	{:else}
		<div class="space-y-3">
			{#each sceneStore.scenes as scene (scene.id)}
				<div
					class="flex items-center gap-4 rounded-card border border-line bg-surface/70 px-4 py-3.5"
				>
					<div class="min-w-0 grow">
						{#if editingId === scene.id}
							<input
								bind:value={draft}
								onblur={() => commitRename(scene.id)}
								onkeydown={(e) => {
									if (e.key === 'Enter') commitRename(scene.id);
									if (e.key === 'Escape') editingId = null;
								}}
								aria-label="Scene name"
								class="h-8 w-full rounded-lg border border-accent bg-surface px-2 font-display text-lg font-semibold text-ink outline-none"
							/>
						{:else}
							<div class="flex items-center gap-2">
								<span class="truncate font-display text-lg font-semibold text-ink"
									>{scene.name}</span
								>
								<button
									type="button"
									onclick={() => startRename(scene.id, scene.name)}
									aria-label="Rename {scene.name}"
									class="text-faint transition-colors hover:text-accent-ink"
								>
									<Icon name="pencil" size={15} />
								</button>
							</div>
							<p class="mt-0.5 truncate text-sm text-muted">{deviceCount(scene.entries.length)}</p>
						{/if}
					</div>

					<button
						type="button"
						onclick={() => sceneStore.apply(scene.id)}
						disabled={sceneStore.applying[scene.id] || !scene.entries.length}
						class="flex h-9 items-center gap-2 rounded-full bg-accent px-4 text-sm font-semibold text-[#04201f] transition hover:brightness-105 disabled:opacity-40"
					>
						<Icon
							name="power"
							size={16}
							class={sceneStore.applying[scene.id] ? 'animate-pulse' : ''}
						/>
						Apply
					</button>
					<button
						type="button"
						onclick={() => sceneStore.remove(scene.id)}
						aria-label="Delete {scene.name}"
						class="text-faint transition-colors hover:text-red-500"
					>
						<Icon name="trash" size={16} />
					</button>
				</div>
			{/each}
		</div>
	{/if}
</div>
