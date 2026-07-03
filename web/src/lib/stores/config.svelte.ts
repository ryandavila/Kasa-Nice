import { getConfig } from '$lib/api/client';
import type { ServerConfig } from '$lib/api/types';

/**
 * Load-once cache of the server's static config (scan subnet, energy rate,
 * location flag). Several tabs each need one field; sharing a single fetch
 * stops every component re-requesting the same immutable data with its own
 * copy of the failure-default policy.
 */
class ConfigStore {
	config = $state<ServerConfig | null>(null);

	private pending: Promise<void> | null = null;

	/** Fetch once; concurrent and repeat callers share the same request. */
	load(): Promise<void> {
		this.pending ??= getConfig()
			.then((cfg) => {
				this.config = cfg;
			})
			.catch(() => {
				this.pending = null; // config is best-effort; retry on next call
			});
		return this.pending;
	}

	get energyRate(): number | null {
		return this.config?.energy_rate ?? null;
	}

	get currency(): string {
		return this.config?.energy_currency || '$';
	}

	/** Defaults true until told otherwise, so the UI doesn't over-warn. */
	get locationConfigured(): boolean {
		return this.config?.location_configured ?? true;
	}

	get scanSubnet(): string | null {
		return this.config?.scan_subnet ?? null;
	}
}

export const configStore = new ConfigStore();
