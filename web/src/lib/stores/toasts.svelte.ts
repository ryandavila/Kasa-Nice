export type ToastKind = 'on' | 'off' | 'info' | 'error';

export interface Toast {
	id: number;
	kind: ToastKind;
	message: string;
}

let nextId = 0;

class ToastStore {
	items = $state<Toast[]>([]);

	push(message: string, kind: ToastKind = 'info', ttl = 2600) {
		const id = nextId++;
		this.items.push({ id, kind, message });
		setTimeout(() => this.dismiss(id), ttl);
	}

	dismiss(id: number) {
		this.items = this.items.filter((t) => t.id !== id);
	}
}

export const toasts = new ToastStore();
