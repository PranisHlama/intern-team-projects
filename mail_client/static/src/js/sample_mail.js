/** @odoo-module **/

// Fictional sample data. No localStorage, IndexedDB, ORM or network calls.
export class SampleMailProvider {
    constructor() {
        this.accounts = [
            { id: 'demo-gmail', name: 'Production', provider: 'gmail', email: 'production@example.test' },
            { id: 'demo-outlook', name: 'Branches', provider: 'outlook', email: 'branches@example.test' },
        ];
        const seeds = [
            ['demo-gmail', 'Green Bean Supplier', 'supplier@example.test', 'Raw-material delivery delayed', 'The green-bean delivery will arrive tomorrow at 10 AM. Please review your production schedule.', false],
            ['demo-outlook', 'Baneshwor Branch', 'baneshwor@example.test', 'Additional roasted beans requested', 'Could you prepare an additional 15 kg of roasted beans for Friday? Please confirm availability.', false],
            ['demo-gmail', 'Inventory Team', 'inventory@example.test', 'Packaging stock update', 'We have 120 packaging bags available. Purchasing has been informed of the replenishment requirement.', true],
            ['demo-outlook', 'Lalitpur Branch', 'lalitpur@example.test', 'Delivery time confirmation', 'Our receiving staff will be available from 8 AM to 11 AM tomorrow.', true],
            ['demo-gmail', 'Purchasing Team', 'purchasing@example.test', 'Supplier quotation received', 'The updated supplier quotation is ready for review. Let us know the required order quantity.', false],
            ['demo-outlook', 'Branch Operations', 'operations@example.test', 'Weekend production estimate', 'Please plan for higher weekend demand. Final branch quantities will follow this afternoon.', true],
            ['demo-gmail', 'Production Lead', 'lead@example.test', 'Batch review complete', 'The latest production batch has completed its quality review.', true],
            ['demo-outlook', 'Dispatch Team', 'dispatch@example.test', 'Morning route update', 'The morning delivery route will visit Lalitpur before Baneshwor.', true],
        ];
        this.messages = seeds.map((s, i) => ({
            id: `sample-${i + 1}`, accountId: s[0], senderName: s[1], sender: s[2],
            subject: s[3], body: s[4], isRead: s[5],
            date: new Date(Date.UTC(2026, 9, 2, 5, 30 - i * 15)).toISOString(),
        }));
        this.drafts = [];
        this.nextId = 1;
    }
    listMessages({ accountId = 'all', query = '', unread = false, offset = 0, limit = 5 } = {}) {
        const term = query.toLowerCase().trim();
        const rows = this.messages.filter(m => (accountId === 'all' || m.accountId === accountId)
            && (!unread || !m.isRead) && `${m.subject} ${m.sender} ${m.senderName}`.toLowerCase().includes(term))
            .sort((a, b) => b.date.localeCompare(a.date));
        return { items: rows.slice(offset, offset + limit).map(({ body, ...headers }) => ({ ...headers, snippet: body.replace(/\s+/g, ' ').slice(0, 150) })), total: rows.length };
    }
    getMessage(id) {
        const m = this.messages.find(m => m.id === id);
        if (!m) throw new Error('Sample message not found.');
        return { ...m };
    }
    setRead(id, isRead) {
        const m = this.messages.find(m => m.id === id);
        if (m) m.isRead = isRead;
    }
    listDrafts(accountId = 'all') {
        return this.drafts.filter(d => accountId === 'all' || d.accountId === accountId).map(d => ({ ...d }));
    }
    saveDraft(draft) {
        if (!this.accounts.some(a => a.id === draft.accountId)) throw new Error('Choose a sample sending account.');
        const saved = { ...draft, id: draft.id || `draft-${this.nextId++}`, date: new Date().toISOString() };
        const index = this.drafts.findIndex(d => d.id === saved.id);
        if (index === -1) this.drafts.push(saved); else this.drafts[index] = saved;
        return saved.id;
    }
    simulateSend(draft) {
        const addresses = draft.to.split(',').map(x => x.trim());
        if (addresses.some(a => !/^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(a))) {
            throw new Error('Enter valid recipient addresses, separated by commas.');
        }
        if (!draft.subject.trim() || !draft.body.trim()) throw new Error('Enter a subject and message.');
        if (!this.accounts.some(a => a.id === draft.accountId)) throw new Error('Choose a sample sending account.');
        this.drafts = this.drafts.filter(d => d.id !== draft.id);
        return { simulated: true }; // Deliberately does not contact a mail provider.
    }
}
