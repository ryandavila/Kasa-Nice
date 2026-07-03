/**
 * Svelte action: dismiss a popover on an outside click or Escape.
 *
 * `close` fires when a click lands outside the node or Escape is pressed;
 * closing an already-closed popover is a harmless no-op, so callers don't need
 * to guard on their open state. Shared by every header/card popover so the
 * dismissal idiom exists once.
 */
export function dismissable(node: HTMLElement, close: () => void) {
	function onClick(e: MouseEvent) {
		if (!node.contains(e.target as Node)) close();
	}
	function onKey(e: KeyboardEvent) {
		if (e.key === 'Escape') close();
	}
	window.addEventListener('click', onClick);
	window.addEventListener('keydown', onKey);
	return {
		destroy() {
			window.removeEventListener('click', onClick);
			window.removeEventListener('keydown', onKey);
		}
	};
}
