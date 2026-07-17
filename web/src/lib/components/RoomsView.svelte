<script lang="ts">
	import type { Device } from '$lib/api/types';
	import { deviceStore } from '$lib/stores/devices.svelte';
	import { groupStore } from '$lib/stores/groups.svelte';
	import Icon from './Icon.svelte';
	import DeviceCard from './DeviceCard.svelte';
	import Toggle from './Toggle.svelte';

	// Any device on anywhere enables the global "Everything off" action.
	const anyOn = $derived(deviceStore.devices.some((d) => d.is_on));

	// Devices the user has starred, in the main list's order.
	const favorites = $derived(deviceStore.devices.filter((d) => groupStore.isFavorite(d.id)));

	// One bucket per room (resolving stored ids to live devices, skipping any that
	// aren't currently known), plus everything not assigned to a room.
	const rooms = $derived(
		groupStore.groups.map((room) => ({
			...room,
			devices: room.device_ids
				.map((id) => deviceStore.devices.find((d) => d.id === id))
				.filter((d): d is Device => d != null)
		}))
	);
	const unassigned = $derived(deviceStore.devices.filter((d) => !groupStore.groupOf(d.id)));

	// Inline "new room" composer.
	let adding = $state(false);
	let newName = $state('');

	async function submitNewRoom() {
		const name = newName.trim();
		if (!name) return;
		await groupStore.createRoom(name);
		newName = '';
		adding = false;
	}

	// Inline rename: which room is being edited, and its draft name.
	let editingId = $state<string | null>(null);
	let draft = $state('');

	function startRename(id: string, name: string) {
		editingId = id;
		draft = name;
	}

	async function commitRename(id: string) {
		const name = draft.trim();
		if (name && name !== rooms.find((r) => r.id === id)?.name) {
			await groupStore.renameRoom(id, name);
		}
		editingId = null;
	}
</script>

<div class="space-y-10">
	<!-- ── Favorites ──────────────────────────────────────────────────────── -->
	{#if favorites.length}
		<section>
			<div class="mb-4 flex items-baseline gap-3">
				<h2
					class="flex items-center gap-1.5 font-display text-sm font-semibold tracking-[0.18em] text-muted uppercase"
				>
					<Icon name="star-filled" size={14} class="text-accent-ink" />
					Favorites
				</h2>
				<span class="h-px grow bg-line"></span>
				<span class="font-mono text-xs text-faint">{favorites.length}</span>
			</div>
			<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
				{#each favorites as device, i (device.id)}
					<DeviceCard {device} delay={i * 45} showRoom />
				{/each}
			</div>
		</section>
	{/if}

	<!-- ── Rooms toolbar ──────────────────────────────────────────────────── -->
	<div class="flex items-center justify-between gap-3">
		<p class="text-sm text-muted">
			{rooms.length}
			{rooms.length === 1 ? 'room' : 'rooms'}
		</p>
		<div class="flex items-center gap-2">
			<button
				type="button"
				onclick={() => deviceStore.setAllPower(deviceStore.devices, false)}
				disabled={!anyOn}
				class="flex h-9 items-center gap-2 rounded-full border border-line bg-surface px-4 text-sm font-medium text-muted transition-colors hover:border-red-500 hover:text-red-500 disabled:opacity-40 disabled:hover:border-line disabled:hover:text-muted"
			>
				<Icon name="power" size={16} />
				Everything off
			</button>
			{#if adding}
				<form
					class="flex items-center gap-2"
					onsubmit={(e) => (e.preventDefault(), submitNewRoom())}
				>
					<!-- svelte-ignore a11y_autofocus -->
					<input
						bind:value={newName}
						autofocus
						placeholder="Room name"
						onkeydown={(e) => e.key === 'Escape' && (adding = false)}
						class="h-9 rounded-full border border-line bg-surface px-4 text-sm text-ink outline-none focus:border-accent"
					/>
					<button
						type="submit"
						class="h-9 rounded-full bg-accent px-4 text-sm font-semibold text-[#04201f] hover:brightness-105"
					>
						Add
					</button>
					<button
						type="button"
						onclick={() => ((adding = false), (newName = ''))}
						aria-label="Cancel"
						class="grid h-9 w-9 place-items-center rounded-full border border-line text-muted hover:text-ink"
					>
						<Icon name="x" size={16} />
					</button>
				</form>
			{:else}
				<button
					type="button"
					onclick={() => (adding = true)}
					class="flex h-9 items-center gap-2 rounded-full border border-line bg-surface px-4 text-sm font-medium text-muted transition-colors hover:border-accent hover:text-accent-ink"
				>
					<Icon name="plus" size={16} />
					New room
				</button>
			{/if}
		</div>
	</div>

	<!-- ── Per-room sections ──────────────────────────────────────────────── -->
	{#each rooms as room (room.id)}
		<section>
			<div class="mb-4 flex items-center gap-3">
				{#if editingId === room.id}
					<input
						bind:value={draft}
						onblur={() => commitRename(room.id)}
						onkeydown={(e) => {
							if (e.key === 'Enter') commitRename(room.id);
							if (e.key === 'Escape') editingId = null;
						}}
						aria-label="Room name"
						class="h-8 rounded-lg border border-accent bg-surface px-2 font-display text-sm font-semibold tracking-[0.12em] text-ink uppercase outline-none"
					/>
				{:else}
					<h2
						class="flex items-center gap-1.5 font-display text-sm font-semibold tracking-[0.18em] text-muted uppercase"
					>
						<Icon name="home" size={14} />
						{room.name}
					</h2>
					<button
						type="button"
						onclick={() => startRename(room.id, room.name)}
						aria-label="Rename {room.name}"
						class="text-faint transition-colors hover:text-accent-ink"
					>
						<Icon name="pencil" size={14} />
					</button>
				{/if}
				<span class="h-px grow bg-line"></span>
				<span class="font-mono text-xs text-faint">{room.devices.length}</span>
				{#if room.devices.length}
					<Toggle
						size="sm"
						checked={room.devices.some((d) => d.is_on)}
						onchange={(on) => deviceStore.setGroupPower(room.id, room.devices, on)}
						label="Toggle all in {room.name}"
					/>
				{/if}
				<button
					type="button"
					onclick={() => groupStore.deleteRoom(room.id)}
					aria-label="Delete {room.name}"
					class="text-faint transition-colors hover:text-red-500"
				>
					<Icon name="trash" size={14} />
				</button>
			</div>
			{#if room.devices.length}
				<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
					{#each room.devices as device, i (device.id)}
						<DeviceCard {device} delay={i * 45} showRoom />
					{/each}
				</div>
			{:else}
				<p
					class="rounded-card border border-dashed border-line bg-surface/40 px-4 py-6 text-center text-sm text-muted"
				>
					No devices yet — assign some from “No room” below.
				</p>
			{/if}
		</section>
	{/each}

	<!-- ── Unassigned ─────────────────────────────────────────────────────── -->
	{#if unassigned.length}
		<section>
			<div class="mb-4 flex items-baseline gap-3">
				<h2 class="font-display text-sm font-semibold tracking-[0.18em] text-muted uppercase">
					{rooms.length ? 'No room' : 'All devices'}
				</h2>
				<span class="h-px grow bg-line"></span>
				<span class="font-mono text-xs text-faint">{unassigned.length}</span>
			</div>
			<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
				{#each unassigned as device, i (device.id)}
					<DeviceCard {device} delay={i * 45} showRoom />
				{/each}
			</div>
		</section>
	{/if}
</div>
