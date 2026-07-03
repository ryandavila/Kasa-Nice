import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';

// Mock the API client so the store is tested in isolation (no network), same
// pattern as groups.svelte.test.ts.
vi.mock('$lib/api/client', () => {
	class ApiError extends Error {
		constructor(
			public status: number,
			message: string
		) {
			super(message);
		}
	}
	return {
		ApiError,
		errorMessage: (e: unknown, fallback = 'Something went wrong') =>
			e instanceof Error ? e.message : fallback,
		restoreBackup: vi.fn(),
		downloadBackupFile: vi.fn(),
		downloadEnergyDb: vi.fn()
	};
});

import * as client from '$lib/api/client';
import { backupStore } from './backup.svelte';

const restoreBackup = client.restoreBackup as Mock;
const downloadBackupFile = client.downloadBackupFile as Mock;
const downloadEnergyDb = client.downloadEnergyDb as Mock;

function fakeFile(content: string, name = 'backup.json'): File {
	return new File([content], name, { type: 'application/json' });
}

const validDoc = {
	backup_version: 1,
	created_at: '2026-01-01T00:00:00Z',
	app_version: '1.1.0',
	groups: [{ id: 'g1', name: 'Den', device_ids: [] }],
	favorites: ['10.0.0.1'],
	scenes: [],
	schedules: [],
	alert_thresholds: { '10.0.0.1': 30 },
	known_devices: []
};

beforeEach(() => {
	vi.clearAllMocks();
	backupStore.pending = null;
	backupStore.restoring = false;
	backupStore.downloadingJson = false;
	backupStore.downloadingEnergyDb = false;
});

describe('downloadJson', () => {
	it('tracks the busy flag around a successful download', async () => {
		downloadBackupFile.mockResolvedValue(undefined);
		const promise = backupStore.downloadJson();
		expect(backupStore.downloadingJson).toBe(true);
		await promise;
		expect(backupStore.downloadingJson).toBe(false);
		expect(downloadBackupFile).toHaveBeenCalled();
	});

	it('clears the busy flag even when the download fails', async () => {
		downloadBackupFile.mockRejectedValue(new Error('network down'));
		await backupStore.downloadJson();
		expect(backupStore.downloadingJson).toBe(false);
	});
});

describe('downloadEnergyDb', () => {
	it('calls the energy-db download and resets the busy flag', async () => {
		downloadEnergyDb.mockResolvedValue(undefined);
		await backupStore.downloadEnergyDb();
		expect(downloadEnergyDb).toHaveBeenCalled();
		expect(backupStore.downloadingEnergyDb).toBe(false);
	});
});

describe('stageFile', () => {
	it('parses a valid backup file into pending', async () => {
		await backupStore.stageFile(fakeFile(JSON.stringify(validDoc)));
		expect(backupStore.pending).toEqual(validDoc);
	});

	it('rejects invalid JSON without setting pending', async () => {
		await backupStore.stageFile(fakeFile('{ not json'));
		expect(backupStore.pending).toBeNull();
	});

	it('rejects JSON missing backup_version without setting pending', async () => {
		await backupStore.stageFile(fakeFile(JSON.stringify({ groups: [] })));
		expect(backupStore.pending).toBeNull();
	});
});

describe('cancelRestore', () => {
	it('clears the pending document', async () => {
		await backupStore.stageFile(fakeFile(JSON.stringify(validDoc)));
		expect(backupStore.pending).not.toBeNull();
		backupStore.cancelRestore();
		expect(backupStore.pending).toBeNull();
	});
});

describe('confirmRestore', () => {
	it('submits the pending document and clears it on success', async () => {
		await backupStore.stageFile(fakeFile(JSON.stringify(validDoc)));
		restoreBackup.mockResolvedValue(validDoc);

		const ok = await backupStore.confirmRestore();

		expect(ok).toBe(true);
		expect(restoreBackup).toHaveBeenCalledWith(validDoc);
		expect(backupStore.pending).toBeNull();
		expect(backupStore.restoring).toBe(false);
	});

	it('keeps pending and reports failure when the server rejects it', async () => {
		await backupStore.stageFile(fakeFile(JSON.stringify(validDoc)));
		restoreBackup.mockRejectedValue(new Error('unsupported backup_version'));

		const ok = await backupStore.confirmRestore();

		expect(ok).toBe(false);
		expect(backupStore.pending).toEqual(validDoc);
		expect(backupStore.restoring).toBe(false);
	});

	it('is a no-op when nothing is staged', async () => {
		const ok = await backupStore.confirmRestore();
		expect(ok).toBe(false);
		expect(restoreBackup).not.toHaveBeenCalled();
	});
});
