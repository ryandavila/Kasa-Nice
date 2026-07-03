<script lang="ts">
	import { dismissable } from '$lib/actions/dismissable';
	import { backupStore } from '$lib/stores/backup.svelte';
	import Icon from './Icon.svelte';

	// The gear button + modal are self-contained here (no parent-managed open
	// state) so any future settings section only needs to add a block below,
	// not thread new props through +page.svelte.
	let open = $state(false);
	let fileInput = $state<HTMLInputElement | undefined>();

	function close() {
		open = false;
		backupStore.cancelRestore();
	}

	function pickFile() {
		fileInput?.click();
	}

	async function onFileChosen(e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		// Reset the input so choosing the same filename again still fires 'change'.
		input.value = '';
		if (file) await backupStore.stageFile(file);
	}

	async function confirmRestore() {
		await backupStore.confirmRestore();
	}

	// Counts for the "here's what will be replaced" confirmation, tolerant of a
	// hand-edited file missing a section (0, not a crash).
	const pendingSummary = $derived.by(() => {
		const doc = backupStore.pending;
		if (!doc) return null;
		return [
			{ label: 'Rooms', count: doc.groups?.length ?? 0 },
			{ label: 'Favorites', count: doc.favorites?.length ?? 0 },
			{ label: 'Scenes', count: doc.scenes?.length ?? 0 },
			{ label: 'Schedules', count: doc.schedules?.length ?? 0 },
			{ label: 'Alert thresholds', count: Object.keys(doc.alert_thresholds ?? {}).length },
			{ label: 'Known devices', count: doc.known_devices?.length ?? 0 }
		];
	});
</script>

<!--
	The trigger button and the modal overlay share one wrapper so `dismissable`
	(bound below) sees the button as part of its own subtree: it closes on any
	click OUTSIDE this node, and without the shared wrapper, the very click that
	sets `open = true` would also bubble to `dismissable`'s window listener and
	immediately close the modal again (the button lives outside a `position:
	fixed` overlay's own DOM subtree otherwise).
-->
<div use:dismissable={close}>
	<button
		type="button"
		onclick={() => (open = true)}
		aria-label="Settings"
		title="Settings"
		class="grid h-10 w-10 place-items-center rounded-full border border-line bg-surface text-muted transition-colors hover:border-accent hover:text-accent-ink"
	>
		<Icon name="settings" size={18} />
	</button>

	{#if open}
		<!-- The backdrop is inside the `dismissable` wrapper (see the comment
		     above), so a backdrop click needs its own explicit close — it won't
		     hit `dismissable`'s "outside click" case. -->
		<div
			class="fixed inset-0 z-40 grid place-items-center bg-black/40 p-4 backdrop-blur-sm"
			onclick={close}
			onkeydown={(e) => e.key === 'Escape' && close()}
			role="presentation"
		>
			<div
				onclick={(e) => e.stopPropagation()}
				role="presentation"
				class="animate-rise max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-card border border-line bg-surface shadow-[0_30px_60px_-20px_var(--glow)]"
			>
				<div class="flex items-center justify-between border-b border-line px-5 py-4">
					<span class="font-display text-lg font-semibold text-ink">Settings</span>
					<button
						type="button"
						onclick={close}
						aria-label="Close settings"
						class="grid h-8 w-8 place-items-center rounded-full text-faint transition-colors hover:bg-raised hover:text-ink"
					>
						<Icon name="x" size={16} />
					</button>
				</div>

				<div class="divide-y divide-line">
					<!-- ── Backup & restore ────────────────────────────────────────── -->
					<section class="px-5 py-4">
						<h3 class="font-display text-sm font-semibold text-ink">Backup & restore</h3>
						<p class="mt-1 text-xs leading-relaxed text-muted">
							Download everything the server persists — rooms, favorites, scenes, schedules, alert
							thresholds, and known devices — as one JSON file, or restore from one.
						</p>

						{#if backupStore.pending && pendingSummary}
							<!-- Confirmation step: nothing is sent to the server until this is
							     accepted, per the feature's "review before restoring" requirement. -->
							<div class="mt-4 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
								<p class="text-sm font-medium text-ink">
									Restoring will REPLACE the server's current data with:
								</p>
								<ul class="mt-2 space-y-1 text-xs text-muted">
									{#each pendingSummary as row (row.label)}
										<li class="flex items-center justify-between">
											<span>{row.label}</span>
											<span class="font-mono tabular-nums text-ink">{row.count}</span>
										</li>
									{/each}
								</ul>
								<p class="mt-2 text-xs text-faint">
									Backup version {backupStore.pending.backup_version} · created {new Date(
										backupStore.pending.created_at
									).toLocaleString()}
								</p>
								<div class="mt-3 flex gap-2">
									<button
										type="button"
										onclick={confirmRestore}
										disabled={backupStore.restoring}
										class="flex items-center gap-2 rounded-full bg-accent px-4 py-2 text-xs font-semibold text-[#04201f] transition hover:brightness-105 disabled:opacity-60"
									>
										{backupStore.restoring ? 'Restoring…' : 'Confirm restore'}
									</button>
									<button
										type="button"
										onclick={() => backupStore.cancelRestore()}
										disabled={backupStore.restoring}
										class="rounded-full border border-line px-4 py-2 text-xs font-medium text-muted transition hover:border-accent hover:text-accent-ink disabled:opacity-60"
									>
										Cancel
									</button>
								</div>
							</div>
						{:else}
							<div class="mt-4 flex flex-col gap-2">
								<button
									type="button"
									onclick={() => backupStore.downloadJson()}
									disabled={backupStore.downloadingJson}
									class="flex items-center gap-2 rounded-xl border border-line px-4 py-2.5 text-sm font-medium text-ink transition hover:border-accent hover:text-accent-ink disabled:opacity-60"
								>
									<Icon name="download" size={16} />
									{backupStore.downloadingJson ? 'Preparing…' : 'Download backup'}
								</button>
								<button
									type="button"
									onclick={pickFile}
									class="flex items-center gap-2 rounded-xl border border-line px-4 py-2.5 text-sm font-medium text-ink transition hover:border-accent hover:text-accent-ink"
								>
									<Icon name="upload" size={16} />
									Restore from file…
								</button>
								<input
									bind:this={fileInput}
									type="file"
									accept="application/json"
									class="hidden"
									onchange={onFileChosen}
								/>
							</div>
						{/if}
					</section>

					<!-- ── Energy history ──────────────────────────────────────────── -->
					<section class="px-5 py-4">
						<h3 class="font-display text-sm font-semibold text-ink">Energy history</h3>
						<p class="mt-1 text-xs leading-relaxed text-muted">
							Download the raw recorded power-sample database (SQLite). Kept separate from the
							backup file above since it can be much larger.
						</p>
						<button
							type="button"
							onclick={() => backupStore.downloadEnergyDb()}
							disabled={backupStore.downloadingEnergyDb}
							class="mt-4 flex items-center gap-2 rounded-xl border border-line px-4 py-2.5 text-sm font-medium text-ink transition hover:border-accent hover:text-accent-ink disabled:opacity-60"
						>
							<Icon name="download" size={16} />
							{backupStore.downloadingEnergyDb ? 'Preparing…' : 'Download energy DB'}
						</button>
					</section>
				</div>
			</div>
		</div>
	{/if}
</div>
