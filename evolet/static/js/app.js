/* Alpine.js app components — loaded in base.html */
/* Additional JS helpers for Evolet */

document.addEventListener('alpine:init', () => {
    Alpine.data('relationSearchWidget', () => ({
        q: '',
        patientCode: '',
        k: 10,
        results: [],
        loading: false,
        err: '',
        searched: false,
        async search() {
            this.err = '';
            this.searched = true;
            const query = (this.q || '').trim();
            if (!query) {
                this.err = 'Enter a search phrase.';
                this.results = [];
                return;
            }
            this.loading = true;
            this.results = [];
            try {
                const params = new URLSearchParams({ q: query, k: String(this.k) });
                if (this.patientCode) {
                    params.set('patient_code', this.patientCode);
                }
                const res = await fetch(`/api/relations/search/?${params.toString()}`, {
                    headers: { Accept: 'application/json' },
                });
                const data = await res.json();
                if (!res.ok) {
                    this.err = data.error || res.statusText || 'Search failed';
                    return;
                }
                this.results = Array.isArray(data.results) ? data.results : [];
            } catch (e) {
                this.err = e instanceof Error ? e.message : String(e);
            } finally {
                this.loading = false;
            }
        },
    }));

    // Global notification handler
    Alpine.store('notifications', {
        items: [],
        add(message, type = 'info') {
            const id = Date.now();
            this.items.push({ id, message, type });
            setTimeout(() => this.remove(id), 5000);
        },
        remove(id) {
            this.items = this.items.filter(n => n.id !== id);
        }
    });
});

// HTMX event handlers
document.addEventListener('htmx:afterRequest', function(evt) {
    if (evt.detail.successful) {
        // Add success animation
        const target = evt.detail.target;
        if (target) {
            target.classList.add('slide-up');
        }
    }
});
