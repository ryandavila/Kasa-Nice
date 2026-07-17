<script lang="ts">
	import { onMount } from 'svelte';
	import { deviceStore } from '$lib/stores/devices.svelte';
	import { groupStore } from '$lib/stores/groups.svelte';
	import { vacationStore } from '$lib/stores/vacation.svelte';
	import Icon from './Icon.svelte';
	import Toggle from './Toggle.svelte';

	/**
	 * Self-contained vacation-mode (presence simulation) settings panel. Loads its
	 * own devices/rooms so it's usable wherever it's mounted, and reads/writes the
	 * whole config through the vacation store. Deliberately decoupled: the
	 * orchestrator can re-mount this inside a settings panel with no edits.
	 */

	onMount(() => {
		vacationStore.load();
		if (!deviceStore.devices.length) deviceStore.load();
		if (!groupStore.loaded) groupStore.load();
	});

	// Local draft, seeded from the store on first load and after each save. Kept
	// separate from the store's status so edits don't fire a request per keystroke;
	// "Save" (or the enable toggle) commits it.
	let devices = $state<string[]>([]);
	let rooms = $state<string[]>([]);
	// A blank start means "sunset / 19:00" — the empty <input> maps to null.
	let start = $state('');
	let end = $state('23:00');
	let minInterval = $state(15);
	let maxInterval = $state(45);
	let seeded = $state(false);

	// Seed the draft once the store has loaded (runs again if the store reloads).
	$effect(() => {
		if (vacationStore.loaded && !seeded) {
			const c = vacationStore.config();
			devices = [...c.device_ids];
			rooms = [...c.room_ids];
			start = c.start_time ?? '';
			end = c.end_time;
			minInterval = c.min_interval_minutes;
			maxInterval = c.max_interval_minutes;
			seeded = true;
		}
	});

	function toggleDevice(id: string) {
		devices = devices.includes(id) ? devices.filter((d) => d !== id) : [...devices, id];
	}

	function toggleRoom(id: string) {
		rooms = rooms.includes(id) ? rooms.filter((r) => r !== id) : [...rooms, id];
	}

	// Draft validity: at least one target, a sane interval, and a valid end time.
	const hasTargets = $derived(devices.length > 0 || rooms.length > 0);
	const intervalValid = $derived(
		Number.isFinite(minInterval) &&
			Number.isFinite(maxInterval) &&
			minInterval >= 1 &&
			maxInterval >= minInterval
	);
	const canSave = $derived(hasTargets && intervalValid && /^\d{2}:\d{2}$/.test(end));

	async function save() {
		if (!canSave) return;
		await vacationStore.save({
			...vacationStore.config(),
			device_ids: devices,
			room_ids: rooms,
			start_time: start === '' ? null : start,
			end_time: end,
			min_interval_minutes: minInterval,
			max_interval_minutes: maxInterval
		});
	}

	function nextSwitchLabel(ts: number): string {
		return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
	}
</script>

<div class="space-y-6">
	<!-- ── Header: enable + live status ───────────────────────────────────── -->
	<div
		class="flex flex-wrap items-center justify-between gap-4 rounded-card border border-line bg-surface/70 px-4 py-3.5"
	>
		<div class="flex items-center gap-3">
			<span
				class="grid h-10 w-10 place-items-center rounded-xl {vacationStore.active
					? 'bg-accent-soft text-accent-ink'
					: 'bg-raised text-faint'}"
			>
				<Icon name="moon" size={20} />
			</span>
			<div>
				<p class="font-display text-base font-semibold text-ink">Vacation mode</p>
				<p class="text-sm text-muted">
					{#if vacationStore.active && vacationStore.status.next_switch_ts}
						Active — next switch around {nextSwitchLabel(vacationStore.status.next_switch_ts)}
					{:else if vacationStore.status.enabled}
						On — waiting for the active window
					{:else}
						Randomly switches lights so the home looks occupied
					{/if}
				</p>
			</div>
		</div>
		<Toggle
			checked={vacationStore.status.enabled}
			onchange={() => vacationStore.toggleEnabled()}
			label="Enable vacation mode"
		/>
	</div>

	<!-- ── Targets: devices + rooms ───────────────────────────────────────── -->
	<div class="flex flex-col gap-2">
		<span class="text-xs font-medium tracking-wide text-faint uppercase">Lights to simulate</span>
		{#if groupStore.groups.length}
			<div class="flex flex-col gap-1.5">
				<span class="text-xs text-muted">Rooms</span>
				<div class="flex flex-wrap gap-1.5">
					{#each groupStore.groups as room (room.id)}
						<button
							type="button"
							onclick={() => toggleRoom(room.id)}
							aria-pressed={rooms.includes(room.id)}
							class="h-9 rounded-lg border px-3 text-sm font-medium transition-colors {rooms.includes(
								room.id
							)
								? 'border-accent bg-accent text-[#04201f]'
								: 'border-line bg-surface text-muted hover:text-ink'}"
						>
							{room.name}
						</button>
					{/each}
				</div>
			</div>
		{/if}
		<div class="flex flex-col gap-1.5">
			<span class="text-xs text-muted">Devices</span>
			<div class="flex flex-wrap gap-1.5">
				{#each deviceStore.devices as device (device.id)}
					<button
						type="button"
						onclick={() => toggleDevice(device.id)}
						aria-pressed={devices.includes(device.id)}
						class="h-9 rounded-lg border px-3 text-sm font-medium transition-colors {devices.includes(
							device.id
						)
							? 'border-accent bg-accent text-[#04201f]'
							: 'border-line bg-surface text-muted hover:text-ink'}"
					>
						{device.alias}
					</button>
				{/each}
			</div>
			{#if !deviceStore.devices.length}
				<p class="text-xs text-faint">No devices yet — discover some first.</p>
			{/if}
		</div>
	</div>

	<!-- ── Active window ──────────────────────────────────────────────────── -->
	<div class="flex flex-wrap items-end gap-5">
		<label class="flex flex-col gap-1.5">
			<span class="text-xs font-medium tracking-wide text-faint uppercase">Start</span>
			<input
				type="time"
				bind:value={start}
				class="h-10 rounded-lg border border-line bg-surface px-3 text-ink outline-none focus:border-accent"
			/>
			<span class="text-xs text-faint">Blank = sunset (or 19:00 without a location)</span>
		</label>
		<label class="flex flex-col gap-1.5">
			<span class="text-xs font-medium tracking-wide text-faint uppercase">End</span>
			<input
				type="time"
				bind:value={end}
				class="h-10 rounded-lg border border-line bg-surface px-3 text-ink outline-none focus:border-accent"
			/>
			<span class="text-xs text-faint">Everything turns off then</span>
		</label>
	</div>

	<!-- ── Switch interval ────────────────────────────────────────────────── -->
	<div class="flex flex-wrap items-end gap-5">
		<label class="flex flex-col gap-1.5">
			<span class="text-xs font-medium tracking-wide text-faint uppercase">Min interval (min)</span>
			<input
				type="number"
				min="1"
				bind:value={minInterval}
				class="h-10 w-32 rounded-lg border border-line bg-surface px-3 text-ink outline-none focus:border-accent"
			/>
		</label>
		<label class="flex flex-col gap-1.5">
			<span class="text-xs font-medium tracking-wide text-faint uppercase">Max interval (min)</span>
			<input
				type="number"
				min="1"
				bind:value={maxInterval}
				class="h-10 w-32 rounded-lg border border-line bg-surface px-3 text-ink outline-none focus:border-accent"
			/>
		</label>
		<p class="pb-2.5 text-xs text-faint">
			Each light waits a random gap in this range between switches.
		</p>
	</div>

	{#if !intervalValid}
		<p class="text-xs text-red-500">Max interval must be at least the min (and both ≥ 1).</p>
	{/if}

	<div class="flex items-center gap-2">
		<button
			type="button"
			onclick={save}
			disabled={!canSave}
			class="h-9 rounded-full bg-accent px-5 text-sm font-semibold text-[#04201f] hover:brightness-105 disabled:opacity-40"
		>
			Save settings
		</button>
		{#if !hasTargets}
			<span class="text-xs text-faint">Pick at least one device or room.</span>
		{/if}
	</div>
</div>
