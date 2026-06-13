type Theme = 'light' | 'dark';

class ThemeStore {
	current = $state<Theme>('light');

	/** Sync with the value the inline app.html script already applied. */
	init() {
		if (typeof document === 'undefined') return;
		this.current = (document.documentElement.dataset.theme as Theme) ?? 'light';
	}

	toggle() {
		this.current = this.current === 'light' ? 'dark' : 'light';
		document.documentElement.dataset.theme = this.current;
		try {
			localStorage.setItem('kasa-theme', this.current);
		} catch {
			// ignore storage failures (private mode, etc.)
		}
	}
}

export const theme = new ThemeStore();
