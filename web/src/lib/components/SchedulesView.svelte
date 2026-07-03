<script lang="ts">
	import { onMount } from 'svelte';
	import type {
		ScheduleAction,
		ScheduleCreate,
		ScheduleKind,
		ScheduleUpdate
	} from '$lib/api/types';
	import { configStore } from '$lib/stores/config.svelte';
	import { deviceStore } from '$lib/stores/devices.svelte';
	import { groupStore } from '$lib/stores/groups.svelte';
	import { scheduleStore } from '$lib/stores/schedules.svelte';
	import { sceneStore } from '$lib/stores/scenes.svelte';
	import Icon from './Icon.svelte';
	import Toggle from './Toggle.svelte';

	// 0=Monday … 6=Sunday, matching the backend's datetime.weekday() convention.
	const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

	// Trigger kinds offered in the composer, with their icon + label.
	const KINDS: { v: ScheduleKind; l: string; icon: 'power' | 'sun' | 'moon' }[] = [
		{ v: 'fixed_time', l: 'Fixed time', icon: 'power' },
		{ v: 'sunrise', l: 'Sunrise', icon: 'sun' },
		{ v: 'sunset', l: 'Sunset', icon: 'moon' },
		{ v: 'once', l: 'Once', icon: 'power' }
	];

	// Whether the server has a location set; sunrise/sunset rules can't fire (and
	// the API rejects creating them) without one, so the composer warns and blocks.
	const locationConfigured = $derived(configStore.locationConfigured);

	onMount(() => {
		scheduleStore.load();
		// Devices and rooms name a rule's target; the page loads them, but load here
		// too so the tab is self-sufficient if opened first. Scenes feed the scene
		// action's picker.
		if (!deviceStore.devices.length) deviceStore.load();
		if (!groupStore.loaded) groupStore.load();
		if (!sceneStore.loaded) sceneStore.load();
		configStore.load();
	});

	// The rule being edited (its id), 'new' while composing, or null when closed.
	let editing = $state<string | null>(null);

	// Composer/editor draft. Target is encoded "type:id" for the <select>.
	let fKind = $state<ScheduleKind>('fixed_time');
	let fTime = $state('18:00');
	let fOffset = $state(0);
	let fAt = $state('');
	let fDays = $state<number[]>([]);
	let fTarget = $state('');
	let fAction = $state<ScheduleAction>('on');
	let fScene = $state('');

	/** A sensible default one-shot datetime: tomorrow at 08:00, in local time. */
	function defaultAt(): string {
		// Tomorrow's local date (no in-place mutation, per svelte/prefer-svelte-reactivity),
		// with a fixed 08:00; the input wants 'YYYY-MM-DDTHH:MM'.
		const d = new Date(Date.now() + 24 * 60 * 60 * 1000);
		const pad = (n: number) => String(n).padStart(2, '0');
		return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T08:00`;
	}

	function resetForm() {
		fKind = 'fixed_time';
		fTime = '18:00';
		fOffset = 0;
		fAt = defaultAt();
		fDays = [];
		fTarget = '';
		fAction = 'on';
		fScene = '';
	}

	function openCreate() {
		resetForm();
		editing = 'new';
	}

	function openEdit(id: string) {
		const rule = scheduleStore.rules.find((r) => r.id === id);
		if (!rule) return;
		fKind = rule.kind;
		fTime = rule.time ?? '18:00';
		fOffset = rule.offset_minutes;
		fAt = rule.at ?? defaultAt();
		fDays = [...rule.days];
		fTarget = rule.target ? `${rule.target.type}:${rule.target.id}` : '';
		fAction = rule.action;
		fScene = rule.scene_id ?? '';
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

	// ── Draft validity ─────────────────────────────────────────────────────────
	const needsDays = $derived(fKind !== 'once');
	const isSun = $derived(fKind === 'sunrise' || fKind === 'sunset');
	const locationBlocked = $derived(isSun && !locationConfigured);

	const triggerValid = $derived.by(() => {
		if (fKind === 'once') return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(fAt);
		if (fKind === 'fixed_time') return /^\d{2}:\d{2}$/.test(fTime);
		return true; // sunrise/sunset need only days + a valid offset (a number)
	});
	const actionValid = $derived(fAction === 'scene' ? fScene !== '' : fTarget !== '');
	const canSave = $derived(
		triggerValid &&
			actionValid &&
			(!needsDays || fDays.length > 0) &&
			Number.isFinite(fOffset) &&
			!locationBlocked
	);

	function parseTarget(value: string): { type: 'device' | 'room'; id: string } {
		// Split on the first ':' only — device/room ids may themselves contain one.
		const i = value.indexOf(':');
		return { type: value.slice(0, i) as 'device' | 'room', id: value.slice(i + 1) };
	}

	/** Assemble the create/update payload for the current draft and kind/action. */
	function draftPayload(): ScheduleCreate {
		const payload: ScheduleCreate = { kind: fKind, action: fAction };
		if (fKind === 'once') payload.at = fAt;
		else {
			payload.days = fDays;
			if (fKind === 'fixed_time') payload.time = fTime;
			else payload.offset_minutes = fOffset; // sunrise/sunset
		}
		if (fAction === 'scene') payload.scene_id = fScene;
		else payload.target = parseTarget(fTarget);
		return payload;
	}

	async function save() {
		if (!canSave) return;
		if (editing === 'new') {
			const created = await scheduleStore.create(draftPayload());
			if (created) editing = null;
		} else if (editing) {
			// Send every field so switching kind/action clears the ones it drops.
			const p = draftPayload();
			const patch: ScheduleUpdate = {
				kind: p.kind,
				time: p.time ?? null,
				days: p.days ?? [],
				offset_minutes: p.offset_minutes ?? 0,
				at: p.at ?? null,
				target: p.target ?? null,
				action: p.action,
				scene_id: p.scene_id ?? null
			};
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

	function offsetLabel(mins: number): string {
		if (mins === 0) return '';
		return mins > 0 ? ` +${mins}m` : ` −${Math.abs(mins)}m`;
	}

	/** The big headline for a rule row: its trigger, in words. */
	function triggerHeadline(rule: (typeof scheduleStore.rules)[number]): string {
		if (rule.kind === 'fixed_time') return rule.time ?? '';
		if (rule.kind === 'sunrise') return `Sunrise${offsetLabel(rule.offset_minutes)}`;
		if (rule.kind === 'sunset') return `Sunset${offsetLabel(rule.offset_minutes)}`;
		// once — show the local date/time it fires at
		return rule.at
			? new Date(rule.at).toLocaleString([], {
					month: 'short',
					day: 'numeric',
					hour: '2-digit',
					minute: '2-digit'
				})
			: 'Once';
	}

	/** The sub-line: what the rule acts on, and (for repeating rules) which days. */
	function detailLine(rule: (typeof scheduleStore.rules)[number]): string {
		const what =
			rule.action === 'scene'
				? (sceneStore.scenes.find((s) => s.id === rule.scene_id)?.name ?? 'Scene')
				: rule.target
					? targetLabel(rule.target.type, rule.target.id)
					: '';
		return rule.kind === 'once' ? what : `${what} · ${daysLabel(rule.days)}`;
	}

	/** The action pill's text, e.g. "Turn on", "Turn off", or "Scene". */
	function actionLabel(action: ScheduleAction): string {
		if (action === 'scene') return 'Scene';
		return `Turn ${action}`;
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
				Create a rule to turn a device or room on or off — or apply a scene — automatically at a set
				time, at sunrise/sunset, or once.
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
									>{triggerHeadline(rule)}</span
								>
								<span
									class="rounded-full px-2 py-0.5 text-xs font-semibold uppercase tracking-wide {rule.action ===
									'off'
										? 'bg-raised text-muted'
										: 'bg-accent-soft text-accent-ink'}"
								>
									{actionLabel(rule.action)}
								</span>
							</div>
							<p class="mt-0.5 truncate text-sm text-muted">
								{detailLine(rule)}
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
		<!-- Trigger kind -->
		<div class="flex flex-col gap-1.5">
			<span class="text-xs font-medium uppercase tracking-wide text-faint">Trigger</span>
			<div class="inline-flex flex-wrap gap-1 rounded-lg border border-line bg-surface p-1">
				{#each KINDS as k (k.v)}
					<button
						type="button"
						onclick={() => (fKind = k.v)}
						class="flex h-8 items-center gap-1.5 rounded-md px-3 text-sm font-medium transition-colors {fKind ===
						k.v
							? 'bg-accent text-[#04201f]'
							: 'text-muted hover:text-ink'}"
					>
						<Icon name={k.icon} size={14} />
						{k.l}
					</button>
				{/each}
			</div>
		</div>

		<div class="flex flex-wrap items-end gap-5">
			<!-- Trigger detail: time / offset / datetime, per kind -->
			{#if fKind === 'fixed_time'}
				<label class="flex flex-col gap-1.5">
					<span class="text-xs font-medium uppercase tracking-wide text-faint">Time</span>
					<input
						type="time"
						bind:value={fTime}
						class="h-10 rounded-lg border border-line bg-surface px-3 text-ink outline-none focus:border-accent"
					/>
				</label>
			{:else if fKind === 'once'}
				<label class="flex flex-col gap-1.5">
					<span class="text-xs font-medium uppercase tracking-wide text-faint">Date &amp; time</span
					>
					<input
						type="datetime-local"
						bind:value={fAt}
						class="h-10 rounded-lg border border-line bg-surface px-3 text-ink outline-none focus:border-accent"
					/>
				</label>
			{:else}
				<label class="flex flex-col gap-1.5">
					<span class="text-xs font-medium uppercase tracking-wide text-faint"
						>Offset (minutes)</span
					>
					<input
						type="number"
						bind:value={fOffset}
						step="5"
						class="h-10 w-32 rounded-lg border border-line bg-surface px-3 text-ink outline-none focus:border-accent"
					/>
				</label>
				<p class="pb-2.5 text-xs text-faint">Negative fires before {fKind}.</p>
			{/if}

			<!-- Action -->
			<label class="flex flex-col gap-1.5">
				<span class="text-xs font-medium uppercase tracking-wide text-faint">Action</span>
				<div class="inline-flex h-10 rounded-lg border border-line bg-surface p-1">
					{#each [{ v: 'on', l: 'Turn on' }, { v: 'off', l: 'Turn off' }, { v: 'scene', l: 'Scene' }] as opt (opt.v)}
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

			<!-- Target (on/off) or scene picker -->
			{#if fAction === 'scene'}
				<label class="flex min-w-48 grow flex-col gap-1.5">
					<span class="text-xs font-medium uppercase tracking-wide text-faint">Scene</span>
					<select
						bind:value={fScene}
						class="h-10 rounded-lg border border-line bg-surface px-3 text-ink outline-none focus:border-accent"
					>
						<option value="" disabled>Choose a scene…</option>
						{#each sceneStore.scenes as scene (scene.id)}
							<option value={scene.id}>{scene.name}</option>
						{/each}
					</select>
				</label>
			{:else}
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
			{/if}
		</div>

		<!-- Location hint for sun rules -->
		{#if locationBlocked}
			<p
				class="flex items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400"
			>
				<Icon name="sun" size={14} class="shrink-0" />
				Set <code class="font-mono">KASA_LATITUDE</code> and
				<code class="font-mono">KASA_LONGITUDE</code> on the server for sunrise/sunset schedules to fire.
			</p>
		{/if}

		<!-- Days (repeating kinds only) -->
		{#if needsDays}
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
		{/if}

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
