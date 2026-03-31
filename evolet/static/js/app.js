/* ─────────────────────────────────────────────────────────────
   Evolet — Alpine.js app components
   Load order: this file is a regular <script> before Alpine defers
   ───────────────────────────────────────────────────────────── */

/* ── Global shell (root x-data on <html>) ── */
function appShell() {
    return {
        darkMode: false,

        initApp() {
            // Restore dark mode from localStorage
            this.darkMode = localStorage.getItem('evolet-dark') === 'true';

            // Active nav-link highlight
            const links = document.querySelectorAll('.nav-link');
            links.forEach(link => {
                if (link.href && window.location.pathname.startsWith(new URL(link.href).pathname) && link.href !== window.location.origin + '/') {
                    link.classList.add('active');
                } else if (link.href && new URL(link.href).pathname === '/' && window.location.pathname === '/') {
                    link.classList.add('active');
                }
            });

            // Keyboard shortcut: / or ⌘K → spotlight
            document.addEventListener('keydown', (e) => {
                const tag = document.activeElement?.tagName?.toLowerCase();
                const isInput = tag === 'input' || tag === 'textarea' || tag === 'select';
                if (!isInput && (e.key === '/' || (e.key === 'k' && (e.metaKey || e.ctrlKey)))) {
                    e.preventDefault();
                    Alpine.store('spotlight').open();
                }
            });
        },

        toggleDark() {
            this.darkMode = !this.darkMode;
            localStorage.setItem('evolet-dark', this.darkMode);
        },
    };
}

/* ─────────────────────────────────────────────────────────────
   Alpine stores — registered on alpine:init
   ───────────────────────────────────────────────────────────── */
document.addEventListener('alpine:init', () => {

    /* ── Toast store ── */
    Alpine.store('toasts', {
        items: [],
        _id: 0,
        add(message, type = 'info', duration = 4500) {
            const id = ++this._id;
            this.items.push({ id, message, type });
            setTimeout(() => this.remove(id), duration);
        },
        remove(id) {
            this.items = this.items.filter(t => t.id !== id);
        },
        success(msg) { this.add(msg, 'success'); },
        error(msg)   { this.add(msg, 'error'); },
        warn(msg)    { this.add(msg, 'warning'); },
        info(msg)    { this.add(msg, 'info'); },
    });

    /* ── Spotlight store ── */
    Alpine.store('spotlight', {
        open: false,
        query: '',
        open() {
            this.open = true;
            this.$nextTick?.(() => document.getElementById('spotlight-input')?.focus());
            // fallback without nextTick
            setTimeout(() => document.getElementById('spotlight-input')?.focus(), 30);
        },
        close() {
            this.open = false;
            this.query = '';
        },
    });

    /* ── Relation search widget ── */
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
            if (!query) { this.err = 'Enter a search phrase.'; this.results = []; return; }
            this.loading = true;
            this.results = [];
            try {
                const params = new URLSearchParams({ q: query, k: String(this.k) });
                if (this.patientCode) params.set('patient_code', this.patientCode);
                const res = await fetch(`/api/relations/search/?${params}`, { headers: { Accept: 'application/json' } });
                const data = await res.json();
                if (!res.ok) { this.err = data.error || res.statusText; return; }
                this.results = Array.isArray(data.results) ? data.results : [];
            } catch (e) {
                this.err = e instanceof Error ? e.message : String(e);
            } finally {
                this.loading = false;
            }
        },
    }));

    /* ── JSON viewer ── */
    Alpine.data('jsonViewer', (rawJson) => ({
        mode: 'structured',   // 'structured' | 'raw'
        formatted: '',

        init() {
            try {
                const obj = typeof rawJson === 'string' ? JSON.parse(rawJson) : rawJson;
                this.formatted = JSON.stringify(obj, null, 2);
            } catch {
                this.formatted = String(rawJson);
            }
        },

        get highlighted() {
            return this.formatted
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                .replace(/"([^"]+)":/g, '<span class="json-key">"$1"</span>:')
                .replace(/: "([^"]*)"/g, ': <span class="json-str">"$1"</span>')
                .replace(/: (\d+\.?\d*)/g, ': <span class="json-num">$1</span>')
                .replace(/: (true|false)/g, ': <span class="json-bool">$1</span>')
                .replace(/: null/g, ': <span class="json-null">null</span>');
        },
    }));

    /* ── Upload handler ── */
    Alpine.data('uploadHandler', () => ({
        dragging: false,
        files: [],
        uploading: false,
        progress: 0,

        handleDrop(e) {
            this.dragging = false;
            const dt = e.dataTransfer;
            if (dt?.files?.length) {
                this.files = Array.from(dt.files);
                const input = document.getElementById('file-upload-input');
                if (input) input.files = dt.files;
            }
        },
        handleFiles(e) {
            this.files = Array.from(e.target.files);
        },
        removeFile(index) {
            this.files.splice(index, 1);
        },
        formatSize(bytes) {
            if (bytes < 1024) return `${bytes} B`;
            if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
            return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
        },
        getIcon(name) {
            if (name.endsWith('.pdf'))  return { label: 'PDF', cls: 'bg-red-500/10 text-red-500 dark:text-red-400' };
            if (name.endsWith('.json')) return { label: 'JSON', cls: 'bg-purple-500/10 text-purple-600 dark:text-purple-400' };
            return { label: 'TXT', cls: 'bg-blue-500/10 text-blue-600 dark:text-blue-400' };
        },
        get totalSize() {
            return this.formatSize(this.files.reduce((s, f) => s + f.size, 0));
        },
    }));

});

    /* ── ETA panel (used on upload result + dashboard) ── */
    Alpine.data('etaPanel', (nPdfs = 1) => ({
        loaded: false,
        nPdfs,
        secPerPdf: 0,
        etaText: '',
        tierLabel: '',
        hwSummary: '',
        tierColor: '',

        async load() {
            try {
                const r = await fetch(`/api/system/?n=${this.nPdfs}`);
                const d = await r.json();
                this.secPerPdf = d.eta_per_pdf_s;
                const total    = d.eta_total_s;
                this.etaText   = total >= 60
                    ? `~${Math.ceil(total / 60)} min`
                    : `~${total}s`;

                const tierMap = {
                    gpu_fast: { label: 'GPU Fast (Ampere+)', color: 'color:#a78bfa' },
                    gpu_std:  { label: 'GPU Standard',       color: 'color:#60a5fa' },
                    cpu:      { label: 'CPU only',           color: 'color:#fb923c' },
                };
                const t = tierMap[d.compute_tier] || { label: d.compute_tier, color: '' };
                this.tierLabel  = t.label;
                this.tierColor  = t.color;

                const gpu = d.gpu || {};
                if (gpu.available) {
                    this.hwSummary = `${gpu.device_name} · ${gpu.total_gb} GB VRAM · ${gpu.free_gb} GB free`;
                } else {
                    this.hwSummary = `${d.cpu_cores}-core CPU · ${d.ram_total_gb} GB RAM`;
                }
                this.loaded = true;
            } catch (e) {
                this.loaded = true;
                this.etaText   = 'Unknown';
                this.tierLabel = 'Detection failed';
                this.hwSummary = '';
            }
        },
    }));

/* ─────────────────────────────────────────────────────────────
   HTMX event hooks
   ───────────────────────────────────────────────────────────── */
document.addEventListener('htmx:afterRequest', (evt) => {
    const { successful, target } = evt.detail;
    if (successful && target) target.classList.add('slide-up');
});

document.addEventListener('htmx:responseError', () => {
    Alpine.store('toasts')?.error('Server request failed. Please try again.');
});
