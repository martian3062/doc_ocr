/* Alpine.js app components — loaded in base.html */
/* Additional JS helpers for Evolet */

document.addEventListener('alpine:init', () => {
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
