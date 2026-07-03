import {
	restoreBackup,
	downloadBackupFile,
	downloadEnergyDb,
	errorMessage as message
} from '$lib/api/client';
import type { BackupDocument } from '$lib/api/types';
import { toasts } from './toasts.svelte';

/**
 * Backup & restore for the Settings panel. Restore is two-step by design (see
 * `SettingsPanel`): a picked file is parsed and held in `pending` for the user
 * to review a summary of what it contains before it's actually sent to
 * `POST /api/backup/restore` — the server validates it again regardless, but
 * the confirmation step is what stops an accidental "replace everything".
 */
class BackupStore {
	downloadingJson = $state(false);
	downloadingEnergyDb = $state(false);
	restoring = $state(false);
	/** A parsed-but-not-yet-submitted restore file, awaiting user confirmation. */
	pending = $state<BackupDocument | null>(null);

	async downloadJson() {
		this.downloadingJson = true;
		try {
			await downloadBackupFile();
		} catch (e) {
			toasts.push(message(e, 'Backup download failed'), 'error');
		} finally {
			this.downloadingJson = false;
		}
	}

	async downloadEnergyDb() {
		this.downloadingEnergyDb = true;
		try {
			await downloadEnergyDb();
		} catch (e) {
			toasts.push(message(e, 'Energy history download failed'), 'error');
		} finally {
			this.downloadingEnergyDb = false;
		}
	}

	/**
	 * Parse a picked file as a backup document and stage it for confirmation.
	 * Only a JSON-parse/shape check happens client-side (the server is the real
	 * validator, at restore time) — this just needs enough structure to render
	 * the "here's what will be replaced" summary.
	 */
	async stageFile(file: File) {
		let text: string;
		try {
			text = await file.text();
		} catch {
			toasts.push("Couldn't read the selected file", 'error');
			return;
		}
		let parsed: unknown;
		try {
			parsed = JSON.parse(text);
		} catch {
			toasts.push('That file is not valid JSON', 'error');
			return;
		}
		if (!parsed || typeof parsed !== 'object' || !('backup_version' in parsed)) {
			toasts.push('That file does not look like a Kasa-Nice backup', 'error');
			return;
		}
		this.pending = parsed as BackupDocument;
	}

	cancelRestore() {
		this.pending = null;
	}

	/** Submit the staged, user-confirmed document. Server-validated regardless. */
	async confirmRestore(): Promise<boolean> {
		if (!this.pending) return false;
		this.restoring = true;
		try {
			await restoreBackup(this.pending);
			toasts.push('Backup restored', 'on');
			this.pending = null;
			return true;
		} catch (e) {
			toasts.push(message(e, 'Restore failed'), 'error');
			return false;
		} finally {
			this.restoring = false;
		}
	}
}

export const backupStore = new BackupStore();
