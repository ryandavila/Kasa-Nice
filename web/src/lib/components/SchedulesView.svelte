<script lang="ts">
	import { onMount } from 'svelte';
	import type { ScheduleAction, ScheduleCreate, ScheduleUpdate } from '$lib/api/types';
	import { deviceStore } from '$lib/stores/devices.svelte';
	import { groupStore } from '$lib/stores/groups.svelte';
	import { scheduleStore } from '$lib/stores/schedules.svelte';
	import Icon from './Icon.svelte';
	import Toggle from './Toggle.svelte';

	// 0=Monday … 6=Sunday, matching the backend's datetime.weekday() convention.
	const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

	onMount(() => {
		scheduleStore.load();
		// Devices and rooms name a rule's target; the page loads them, but load here
		// too so the tab is self-sufficient if opened first.
		if (!deviceStore.devices.length) deviceStore.load();
		if (!groupStore.loaded) groupStore.load();
	});

	// The rule being edited (its id), 'new' while composing, or null when closed.
	let editing = $state<string | null>(null);

	// Composer/editor draft. Target is encoded "type:id" for the <select>.
	let fTime = $state('18:00');
	let fDays = $state<number[]>([]);
	let fTarget = $state('');
	let fAction = $state<ScheduleAction>('on');

	function resetForm() {
		fTime = '18:00';
		fDays = [];
		fTarget = '';
		fAction = 'on';
	}

	function openCreate() {
		resetForm();
		editing = 'new';
	}

	function openEdit(id: string) {
		const rule = scheduleStore.rules.find((r) => r.id === id);
		if (!rule) return;
		fTime = rule.time;
		fDays = [...rule.days];
		fTarget = `${rule.target.type}:${rule.target.id}`;
		fAction = rule.action;
		editing = id;
	}

	function cancel() {
		editing = null;
	}

	function toggleDay(d: number) {
		fDays = fDays.includes(d) ? fDays.filter((x) => x !== d) : [...fDays, d].sort();
	}

	function selectAllDays() {
		fDays = fDays.length === 7 ? [] : [0, 1, 2, 3, 4, 5, 6];
	}

	const canSave = $derived(fDays.length > 0 && fTarget !== '' && /^\d{2}:\d{2}$/.test(fTime));

	function parseTarget(value: string): { type: 'device' | 'room'; id: string } {
		// Split on the first ':' only — device/room ids may themselves contain one.
		const i = value.indexOf(':');
		return { type: value.slice(0, i) as 'device' | 'room', id: value.slice(i + 1) };
	}

	async function save() {
		if (!canSave) return;
		const target = parseTarget(fTarget);
		if (editing === 'new') {
			const payload: ScheduleCreate = { time: fTime, days: fDays, target, action: fAction };
			const created = await scheduleStore.create(payload);
			if (created) editing = null;
		} else if (editing) {
			const patch: ScheduleUpdate = { time: fTime, days: fDays, target, action: fAction };
			const updated = await scheduleStore.update(editing, patch);
			if (updated) editing = null;
		}
	}

	// ── Display helpers ──────────────────────────────────────────────────────
	function targetLabel(type: string, id: string): string {
		if (type === 'device') {
			return deviceStore.devices.find((d) => d.id === id)?.alias ?? id;
		}
		return groupStore.groups.find((g) => g.id === id)?.name ?? id;
	}

	function daysLabel(days: number[]): string {
		if (days.length === 7) return 'Every day';
		if (days.length === 5 && [0, 1, 2, 3, 4].every((d) => days.includes(d))) return 'Weekdays';
		if (days.length === 2 && days.includes(5) && days.includes(6)) return 'Weekends';
		return days.map((d) => DAY_LABELS[d]).join(', ');
	}

	function lastFiredLabel(ts: number, result: string): string {
		const when = new Date(ts * 1000).toLocaleString([], {
			month: 'short',
			day: 'numeric',
			hour: '2-digit',
			minute: '2-digit'
		});
		return result === 'ok' ? `Last run ${when}` : `Last run ${when} · ${result}`;
	}
</script>

<div class="space-y-6">
	<!-- ── Toolbar ────────────────────────────────────────────────────────── -->
	<div class="flex items-center justify-between gap-3">
		<p class="text-sm text-muted">
			{scheduleStore.rules.length}
			{scheduleStore.rules.length === 1 ? 'schedule' : 'schedules'}
		</p>
		{#if editing !== 'new'}
			<button
				type="button"
				onclick={openCreate}
				class="flex h-9 items-center gap-2 rounded-full border border-line bg-surface px-4 text-sm font-medium text-muted transition-colors hover:border-accent hover:text-accent-ink"
			>
				<Icon name="plus" size={16} />
				New schedule
			</button>
		{/if}
	</div>

	<!-- ── Composer / editor ──────────────────────────────────────────────── -->
	{#if editing === 'new'}
		{@render editorForm()}
	{/if}

	<!-- ── Rule list ──────────────────────────────────────────────────────── -->
	{#if !scheduleStore.rules.length && editing !== 'new'}
		<div class="rounded-card border border-dashed border-line bg-surface/40 p-12 text-center">
			<span
				class="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-accent-soft text-accent-ink"
			>
				<Icon name="power" size={26} />
			</span>
			<p class="mt-4 font-display text-lg text-ink">No schedules yet</p>
			<p class="mx-auto mt-1 max-w-xs text-sm text-muted">
				Create a rule to turn a device or room on or off automatically at a set time.
			</p>
			<button
				type="button"
				onclick={openCreate}
				class="mt-5 rounded-full bg-accent px-5 py-2.5 text-sm font-semibold text-[#04201f] hover:brightness-105"
			>
				New schedule
			</button>
		</div>
	{:else}
		<div class="space-y-3">
			{#each scheduleStore.rules as rule (rule.id)}
				{#if editing === rule.id}
					{@render editorForm()}
				{:else}
					<div
						class="flex items-center gap-4 rounded-card border border-line bg-surface/70 px-4 py-3.5 {rule.enabled
							? ''
							: 'opacity-60'}"
					>
						<Toggle
							checked={rule.enabled}
							onchange={() => scheduleStore.toggleEnabled(rule.id)}
							label="Enable schedule"
						/>
						<div class="min-w-0 grow">
							<div class="flex items-baseline gap-2">
								<span class="font-display text-lg font-semibold tabular-nums text-ink"
									>{rule.time}</span
								>
								<span
									class="rounded-full px-2 py-0.5 text-xs font-semibold uppercase tracking-wide {rule.action ===
									'on'
										? 'bg-accent-soft text-accent-ink'
										: 'bg-raised text-muted'}"
								>
									Turn {rule.action}
								</span>
							</div>
							<p class="mt-0.5 truncate text-sm text-muted">
								{targetLabel(rule.target.type, rule.target.id)} · {daysLabel(rule.days)}
							</p>
							{#if rule.last_fired}
								<p class="mt-0.5 truncate text-xs text-faint">
									{lastFiredLabel(rule.last_fired.ts, rule.last_fired.result)}
								</p>
							{/if}
						</div>
						<button
							type="button"
							onclick={() => openEdit(rule.id)}
							aria-label="Edit schedule"
							class="text-faint transition-colors hover:text-accent-ink"
						>
							<Icon name="pencil" size={16} />
						</button>
						<button
							type="button"
							onclick={() => scheduleStore.remove(rule.id)}
							aria-label="Delete schedule"
							class="text-faint transition-colors hover:text-red-500"
						>
							<Icon name="trash" size={16} />
						</button>
					</div>
				{/if}
			{/each}
		</div>
	{/if}
</div>

<!-- The create and edit flows share one form; `editing` decides which rule it -->
<!-- writes back to. -->
{#snippet editorForm()}
	<form
		class="space-y-5 rounded-card border border-accent/40 bg-surface/70 p-5"
		onsubmit={(e) => (e.preventDefault(), save())}
	>
		<div class="flex flex-wrap items-end gap-5">
			<label class="flex flex-col gap-1.5">
				<span class="text-xs font-medium uppercase tracking-wide text-faint">Time</span>
				<input
					type="time"
					bind:value={fTime}
					class="h-10 rounded-lg border border-line bg-surface px-3 text-ink outline-none focus:border-accent"
				/>
			</label>

			<label class="flex flex-col gap-1.5">
				<span class="text-xs font-medium uppercase tracking-wide text-faint">Action</span>
				<div class="inline-flex h-10 rounded-lg border border-line bg-surface p-1">
					{#each [{ v: 'on', l: 'Turn on' }, { v: 'off', l: 'Turn off' }] as opt (opt.v)}
						<button
							type="button"
							onclick={() => (fAction = opt.v as ScheduleAction)}
							class="rounded-md px-3 text-sm font-medium transition-colors {fAction === opt.v
								? 'bg-accent text-[#04201f]'
								: 'text-muted hover:text-ink'}"
						>
							{opt.l}
						</button>
					{/each}
				</div>
			</label>

			<label class="flex min-w-48 grow flex-col gap-1.5">
				<span class="text-xs font-medium uppercase tracking-wide text-faint">Target</span>
				<select
					bind:value={fTarget}
					class="h-10 rounded-lg border border-line bg-surface px-3 text-ink outline-none focus:border-accent"
				>
					<option value="" disabled>Choose a device or room…</option>
					{#if groupStore.groups.length}
						<optgroup label="Rooms">
							{#each groupStore.groups as room (room.id)}
								<option value={`room:${room.id}`}>{room.name}</option>
							{/each}
						</optgroup>
					{/if}
					{#if deviceStore.devices.length}
						<optgroup label="Devices">
							{#each deviceStore.devices as device (device.id)}
								<option value={`device:${device.id}`}>{device.alias}</option>
							{/each}
						</optgroup>
					{/if}
				</select>
			</label>
		</div>

		<div class="flex flex-col gap-1.5">
			<div class="flex items-center justify-between">
				<span class="text-xs font-medium uppercase tracking-wide text-faint">Days</span>
				<button
					type="button"
					onclick={selectAllDays}
					class="text-xs font-medium text-muted transition-colors hover:text-accent-ink"
				>
					{fDays.length === 7 ? 'Clear all' : 'Every day'}
				</button>
			</div>
			<div class="flex flex-wrap gap-1.5">
				{#each DAY_LABELS as label, d (d)}
					<button
						type="button"
						onclick={() => toggleDay(d)}
						aria-pressed={fDays.includes(d)}
						class="h-9 w-12 rounded-lg border text-sm font-medium transition-colors {fDays.includes(
							d
						)
							? 'border-accent bg-accent text-[#04201f]'
							: 'border-line bg-surface text-muted hover:text-ink'}"
					>
						{label}
					</button>
				{/each}
			</div>
		</div>

		<div class="flex items-center gap-2">
			<button
				type="submit"
				disabled={!canSave}
				class="h-9 rounded-full bg-accent px-5 text-sm font-semibold text-[#04201f] hover:brightness-105 disabled:opacity-40"
			>
				{editing === 'new' ? 'Create' : 'Save'}
			</button>
			<button
				type="button"
				onclick={cancel}
				class="flex h-9 items-center gap-1.5 rounded-full border border-line px-4 text-sm font-medium text-muted hover:text-ink"
			>
				Cancel
			</button>
		</div>
	</form>
{/snippet}
