/** @odoo-module **/

import { Component, proxy, onWillStart, onWillUnmount } from '@odoo/owl';
import { registry } from '@web/core/registry';
import { useService } from '@web/core/utils/hooks';
import { SampleMailProvider } from './sample_mail';

export class ManufacturingMailClient extends Component {
    static template = 'mail_client.Client';

    setup() {
        this.orm = useService('orm');
        this.action = useService('action');
        this.sample = new SampleMailProvider();
        this.live = { generation: 0, streams: [], pages: [], base: 0 };
        this.searchTimer = null;
        this.state = proxy({
            demo: new URLSearchParams(window.location.search).has('debug') && new URLSearchParams(window.location.search).get('mail_sample') === '1', accounts: [], loading: false, error: '', notice: '',
            mailboxFailures: [], accountId: 'all', folder: 'inbox', query: '', unread: false,
            page: 0, rows: [], total: 0, selected: null, composer: null,
            mailboxesExpanded: false, expandedMailboxIds: [],
            dirty: false, showAccountForm: false, saving: false, composerExpanded: false, liveBusy: false, liveHasNext: false, liveBase: 0,
            accountForm: { name: '', provider: 'gmail', email: '' },
        });
        // Await only metadata: provider sockets/API calls must not block the first render.
        onWillStart(async () => { if (await this.loadAccounts()) void this.refresh(); });
        onWillUnmount(() => { clearTimeout(this.searchTimer); this.live.generation++; });
    }
    get accounts() { return this.state.demo ? this.sample.accounts : this.state.accounts; }
    get heading() {
        if (this.state.folder === 'sent') return 'Sent';
        if (this.state.folder === 'drafts') return this.state.accountId === 'all' ? 'Drafts' : `${this.accounts.find(a => a.id === this.state.accountId)?.name || 'Account'} Drafts`;
        if (this.state.accountId === 'all') return 'All Inbox';
        return `${this.accounts.find(a => a.id === this.state.accountId)?.name || 'Account'} Inbox`;
    }
    get rangeLabel() {
        if (!this.state.demo) return this.state.rows.length ? `Page ${this.state.page + 1} · ${this.state.rows.length} ${this.state.rows.length === 1 ? 'message' : 'messages'}` : '0 messages';
        if (!this.state.total) return '0 messages';
        return `${this.state.page * 5 + 1}–${Math.min((this.state.page + 1) * 5, this.state.total)} of ${this.state.total}`;
    }
    supports(account, capability) {
        return !!account && (this.state.demo || (account.capabilities?.[capability] ?? (account.provider !== 'pop3')));
    }
    providerLabel(account) { return {gmail: 'Gmail', outlook: 'Outlook', pop3: 'POP3'}[account?.provider] || 'Account'; }
    get viewAccounts() { return this.accounts.filter(a => (this.state.accountId === 'all' || a.id === this.state.accountId) && (this.state.demo || a.state === 'connected')); }
    get canSearch() { return (this.state.demo || this.state.folder !== 'drafts') && this.viewAccounts.every(a => this.supports(a, 'search')); }
    get canFilterUnread() { return this.viewAccounts.every(a => this.supports(a, 'read_state')); }
    get disconnectedInboxNames() { return !this.state.demo && this.state.accountId === 'all' && this.state.folder === 'inbox' ? this.accounts.filter(a => a.state !== 'connected').map(a => a.name).join(', ') : ''; }
    get hasPOP3() { return this.viewAccounts.some(a => a.provider === 'pop3'); }
    get hasDraftAccounts() { return this.accounts.some(a => this.supports(a, 'drafts')); }
    get sendingAccounts() { return this.accounts.filter(a => (this.state.demo || a.state === 'connected') && this.supports(a, 'send')); }
    get canSaveProviderDraft() { return this.state.demo || this.supports(this.accounts.find(a => a.id === this.state.composer?.accountId), 'drafts'); }
    get canCompose() { return this.state.demo || !!this.sendingAccounts.length; }
    get previousDisabled() { return this.state.demo ? this.state.page === 0 : this.state.page <= this.state.liveBase; }
    get nextDisabled() { return this.state.demo ? this.state.page + 1 >= this.pageCount : !this.state.liveHasNext; }
    toggleMailboxes() { this.state.mailboxesExpanded = !this.state.mailboxesExpanded; }
    mailboxExpanded(id) { return this.state.expandedMailboxIds.includes(id); }
    toggleMailbox(id) {
        this.state.expandedMailboxIds = this.mailboxExpanded(id) ? this.state.expandedMailboxIds.filter(value => value !== id) : [...this.state.expandedMailboxIds, id];
    }
    accountName(id) { return this.accounts.find(a => a.id === id)?.name || 'Account'; }
    unreadCount(accountId) {
        return this.state.demo ? this.sample.messages.filter(m => !m.isRead && (accountId === 'all' || m.accountId === accountId)).length : 0;
    }
    preview(row) { return (row.snippet || row.body || '').replace(/\s+/g, ' ').trim().slice(0, 150); }
    backToInbox() { this.state.selected = null; }
    get pageCount() { return Math.max(1, Math.ceil(this.state.total / 5)); }
    get selectedAccount() { return this.accounts.find(a => a.id === this.state.selected?.accountId); }
    accountEmail(id) { return this.accounts.find(a => a.id === id)?.email || ''; }
    formatDate(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
    async loadAccounts() {
        this.state.loading = true;
        try {
            this.state.accounts = await this.orm.call('mail_client.account', 'list_client_accounts', []);
            return true;
        } catch {
            this.state.error = 'Could not load account setups. Check your Mail Client access and try Refresh mail.';
            return false;
        } finally { this.state.loading = false; }
    }
    async reloadAccounts() {
        this.state.error = '';
        if (!await this.loadAccounts()) return;
        if (!this.accounts.some(a => a.id === this.state.accountId)) this.state.accountId = 'all';
        return this.refresh();
    }
    canLeaveComposer() {
        if (this.state.saving) return false;
        return !this.state.dirty || window.confirm(this.state.demo ? 'Discard unsaved changes? Saved sample drafts remain until this page reloads.' : 'Discard unsaved changes? Saved drafts remain at the provider.');
    }
    changeView(accountId, folder = 'inbox') {
        if (!this.canLeaveComposer()) return;
        if (accountId !== 'all') {
            this.state.mailboxesExpanded = true;
            if (!this.mailboxExpanded(accountId)) this.state.expandedMailboxIds = [...this.state.expandedMailboxIds, accountId];
        }
        Object.assign(this.state, { accountId, folder, page: 0, selected: null, composer: null, dirty: false });
        // Determine which accounts will be used for streams (same filter as refreshLive)
        const streamAccounts = this.accounts.filter(a => a.state === 'connected' && (accountId === 'all' || accountId === a.id) && this.supports(a, folder === 'drafts' ? 'drafts' : folder));
        if (streamAccounts.some(a => !this.supports(a, 'search'))) this.state.query = '';
        if (streamAccounts.some(a => !this.supports(a, 'read_state'))) this.state.unread = false;
        this.refresh();
    }
    toggleMode() {
        if (!this.canLeaveComposer()) return;
        Object.assign(this.state, { demo: !this.state.demo, accountId: 'all', folder: 'inbox', query: '', unread: false,
            page: 0, selected: null, composer: null, dirty: false, notice: '' });
        this.refresh();
    }
    refresh() {
        if (!this.state.demo) {
            return this.refreshLive(); // Only connected providers; never show sample data here.
        }
        if (this.state.folder === 'drafts') {
            const all = this.sample.listDrafts(this.state.accountId).filter(d =>
                `${d.to} ${d.subject}`.toLowerCase().includes(this.state.query.toLowerCase().trim()));
            this.state.total = all.length;
            this.state.page = Math.min(this.state.page, Math.max(0, Math.ceil(all.length / 5) - 1));
            this.state.rows = all.slice(this.state.page * 5, this.state.page * 5 + 5);
        } else {
            let data = this.sample.listMessages({ accountId: this.state.accountId, query: this.state.query,
                unread: this.state.unread, offset: this.state.page * 5 });
            if (this.state.page && !data.items.length) {
                this.state.page = 0;
                data = this.sample.listMessages({ accountId: this.state.accountId, query: this.state.query, unread: this.state.unread });
            }
            this.state.rows = data.items; this.state.total = data.total;
        }
    }
    search(value) {
        if (!this.canSearch) return;
        this.state.query = value; this.state.page = 0;
        if (this.state.demo) return this.refresh();
        clearTimeout(this.searchTimer);
        // Invalidate in-flight responses immediately, even during the debounce interval.
        this.live.generation++; this.state.liveBusy = false;
        this.searchTimer = setTimeout(() => this.refreshLive(), 400);
    }
    toggleUnread() { if (!this.canFilterUnread) return; this.state.unread = !this.state.unread; this.state.page = 0; this.refresh(); }
    paginate(delta) { if (!this.state.demo) return this.paginateLive(delta); this.state.page = Math.max(0, Math.min(this.pageCount - 1, this.state.page + delta)); this.refresh(); }
    openRow(row) {
        if (!this.state.demo) return this.openLiveRow(row);
        if (!this.canLeaveComposer()) return;
        this.state.dirty = false;
        if (this.state.folder === 'drafts') {
            this.state.composer = { ...row }; this.state.composerExpanded = false; this.state.selected = null;
        } else {
            this.sample.setRead(row.id, true);
            this.state.selected = this.sample.getMessage(row.id);
            this.state.composer = null;
            this.refresh();
        }
    }
    markUnread() {
        if (!this.state.demo) return this.markLiveUnread();
        const message = this.state.selected;
        if (!message) return;
        this.sample.setRead(message.id, false); this.state.selected = null; this.refresh();
    }
    compose(reply = false) {
        if (!this.canCompose || !this.canLeaveComposer()) return;
        if (reply && !this.supports(this.selectedAccount, 'reply')) return;
        const message = reply ? this.state.selected : null;
        const accountId = message?.accountId || (this.sendingAccounts.some(a => a.id === this.state.accountId) ? this.state.accountId : this.sendingAccounts[0].id);
        this.state.composer = {
            id: null, accountId, to: message?.replyTo || message?.sender || '',
            subject: message ? (/^re:/i.test(message.subject) ? message.subject : `Re: ${message.subject}`) : '',
            body: '', replyToId: message?.id || null,
        };
        this.state.dirty = false; this.state.composerExpanded = false; this.state.error = ''; this.state.notice = '';
    }
    editDraft(field, value) { if (field === 'accountId' && !this.state.demo) value = Number(value); this.state.composer[field] = value; this.state.dirty = true; }
    saveDraft() {
        if (!this.state.demo) return this.saveLiveDraft();
        try {
            this.state.composer.id = this.sample.saveDraft({ ...this.state.composer });
            this.state.dirty = false;
            this.state.notice = 'Sample draft saved in memory. It will disappear when this page reloads.';
            this.state.error = ''; this.refresh();
        } catch (error) { this.state.error = error.message; }
    }
    simulateSend() {
        if (!this.state.demo) return this.sendLiveDraft();
        try {
            this.sample.simulateSend({ ...this.state.composer });
            this.state.composer = null; this.state.dirty = false; this.state.error = '';
            this.state.notice = 'Simulation complete. No email was sent.'; this.refresh();
        } catch (error) { this.state.error = error.message; }
    }
    composerKeydown(ev) {
        if (ev.key === 'Escape') {
            ev.preventDefault(); this.closeComposer();
        } else if (ev.key === 'Tab') {
            const focusable = Array.from(ev.currentTarget.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)'));
            const first = focusable[0]; const last = focusable[focusable.length - 1];
            if (ev.shiftKey && ev.target === first) { ev.preventDefault(); last?.focus(); }
            else if (!ev.shiftKey && ev.target === last) { ev.preventDefault(); first?.focus(); }
        }
    }
    closeComposer() { if (this.state.saving) return; if (this.canLeaveComposer()) { this.state.composer = null; this.state.dirty = false; } }
    async connectAccount(id) {
        if (!this.canLeaveComposer()) return;
        try {
            const action = await this.orm.call('mail_client.account', 'action_connect', [[id]]);
            await this.action.doAction(action);
            if (this.state.accounts.find(a => a.id === id)?.provider === 'pop3') await this.reloadAccounts();
        } catch (error) { this.liveError(error, 'Could not connect the account. Check provider setup.'); }
    }
    async disconnectAccount(id) {
        if (!this.canLeaveComposer() || !window.confirm('Disconnect this account from Odoo? Provider mail will remain intact.')) return;
        try {
            await this.orm.call('mail_client.account', 'action_disconnect', [[id]]);
            this.state.selected = null; this.state.composer = null; this.state.dirty = false;
            await this.reloadAccounts();
        } catch (error) { this.liveError(error, 'Could not disconnect this account.'); }
    }
    liveError(error, fallback) {
        this.state.error = error?.data?.message || error?.message || fallback;
    }
    async providerPage(stream) {
        const args = this.state.folder === 'drafts' ? [[stream.accountId], stream.cursor, 10] :
            [[stream.accountId], this.state.folder, stream.cursor, 10, this.state.query, this.state.unread];
        const result = await this.orm.call('mail_client.account', this.state.folder === 'drafts' ? 'list_drafts' : 'list_messages', args);
        // Keep only lightweight headers in page buffers; bodies load when a row opens.
        stream.queue = result.items.map(row => ({ ...row, accountId: stream.accountId }));
        stream.cursor = result.next_cursor || null; stream.started = true;
    }
    async buildLivePage(generation) {
        const items = [];
        for (let i = 0; i < 5; i++) {
            const pending = this.live.streams.filter(s => !s.queue.length && (!s.started || s.cursor));
            const results = await Promise.allSettled(pending.map(s => this.providerPage(s)));
            if (generation !== this.live.generation) return null;
            // One unavailable account must not discard healthy-account messages.
            results.forEach((result, index) => {
                if (result.status !== 'rejected') return;
                const stream = pending[index];
                stream.queue = []; stream.cursor = null; stream.started = true;
                const error = result.reason;
                this.state.mailboxFailures = [...this.state.mailboxFailures, {
                    accountId: stream.accountId, name: this.accountName(stream.accountId),
                    message: error?.data?.message || error?.message || 'Could not load this mailbox.',
                }];
            });
            const available = this.live.streams.filter(s => s.queue.length);
            if (!available.length) break;
            const timestamp = row => Number.isNaN(Date.parse(row.date)) ? 0 : Date.parse(row.date);
            available.sort((a, b) => timestamp(b.queue[0]) - timestamp(a.queue[0]));
            items.push(available[0].queue.shift());
        }
        return items;
    }
    async refreshLive() {
        clearTimeout(this.searchTimer);
        const generation = ++this.live.generation;
        this.state.error = ''; this.state.mailboxFailures = [];
        // Determine which accounts will be used for streams (same filter as below)
        const streamAccounts = this.accounts.filter(a => a.state === 'connected' && (this.state.accountId === 'all' || this.state.accountId === a.id) && this.supports(a, this.state.folder === 'drafts' ? 'drafts' : this.state.folder));
        // Connection metadata can change on Refresh; remove filters a newly
        // connected POP3 stream cannot accept, including stale Gmail filters.
        // Use streamAccounts (not viewAccounts) to ensure POP3 accounts are considered.
        if (streamAccounts.some(a => !this.supports(a, 'search'))) this.state.query = '';
        if (streamAccounts.some(a => !this.supports(a, 'read_state'))) this.state.unread = false;
        this.live.streams = streamAccounts.map(a => ({ accountId: a.id, queue: [], cursor: null, started: false }));
        this.live.pages = []; this.live.base = 0;
        Object.assign(this.state, { rows: [], total: 0, page: 0, liveBase: 0, liveHasNext: false, liveBusy: true });
        try {
            const rows = await this.buildLivePage(generation);
            if (rows === null) return;
            this.live.pages.push(rows); this.state.rows = rows;
            this.state.liveHasNext = this.live.streams.some(s => s.queue.length || s.cursor);
        } catch (error) {
            if (generation === this.live.generation) this.liveError(error, 'Could not fetch mailbox.');
        } finally { if (generation === this.live.generation) this.state.liveBusy = false; }
    }
    async paginateLive(delta) {
        if (this.state.liveBusy || (delta < 0 ? this.previousDisabled : this.nextDisabled)) return;
        const target = this.state.page + delta;
        const cached = this.live.pages[target - this.live.base];
        if (cached) {
            this.state.page = target; this.state.rows = cached;
            this.state.liveHasNext = target - this.live.base < this.live.pages.length - 1 || this.live.streams.some(s => s.queue.length || s.cursor);
            return;
        }
        const generation = this.live.generation;
        this.state.liveBusy = true;
        try {
            const rows = await this.buildLivePage(generation);
            if (rows === null) return;
            this.live.pages.push(rows);
            // Bound browser-memory history; no complete mailbox copy is retained.
            if (this.live.pages.length > 20) { this.live.pages.shift(); this.live.base++; }
            this.state.liveBase = this.live.base; this.state.page = target; this.state.rows = rows;
            this.state.liveHasNext = this.live.streams.some(s => s.queue.length || s.cursor);
        } catch (error) { if (generation === this.live.generation) { this.state.liveHasNext = false; this.liveError(error, 'Could not load the next page. Refresh before retrying.'); } }
        finally { if (generation === this.live.generation) this.state.liveBusy = false; }
    }
    async openLiveRow(row) {
        if (this.state.liveBusy || !this.canLeaveComposer()) return;
        const generation = this.live.generation;
        this.state.liveBusy = true; this.state.error = '';
        try {
            const draft = this.state.folder === 'drafts';
            const item = await this.orm.call('mail_client.account', draft ? 'get_draft' : 'get_message', [[row.accountId], row.id]);
            if (generation !== this.live.generation) return;
            this.state.dirty = false;
            if (draft) { this.state.composer = { ...item, accountId: row.accountId, replyToId: null }; this.state.selected = null; }
            else {
                this.state.selected = { ...item, accountId: row.accountId, externalImagesLoaded: false }; this.state.composer = null;
                if (!item.isRead && this.supports(this.selectedAccount, 'read_state')) {
                    await this.orm.call('mail_client.account', 'set_read', [[row.accountId], row.id, true]);
                    row.isRead = true;
                }
            }
        } catch (error) { if (generation === this.live.generation) this.liveError(error, 'Could not open the message.'); }
        finally { if (generation === this.live.generation) this.state.liveBusy = false; }
    }
    toggleExternalImages() {
        const item=this.state.selected;
        if (item?.hasExternalImages && item.bodyDocumentExternal) item.externalImagesLoaded=!item.externalImagesLoaded;
    }
    async markLiveUnread() {
        const item = this.state.selected;
        if (!item || this.state.liveBusy || !this.supports(this.selectedAccount, 'read_state')) return;
        this.state.liveBusy = true;
        try {
            await this.orm.call('mail_client.account', 'set_read', [[item.accountId], item.id, false]);
            this.state.selected = null; await this.refreshLive();
        } catch (error) { this.liveError(error, 'Could not update the read status.'); }
        finally { this.state.liveBusy = false; }
    }
    async persistLiveDraft() {
        const c = this.state.composer;
        const result = await this.orm.call('mail_client.account', 'save_draft', [[c.accountId],
            { to: c.to, subject: c.subject, body: c.body }, c.id || null, c.replyToId || null]);
        c.id = result.id; this.state.dirty = false;
        return { accountId: c.accountId, id: result.id };
    }
    async saveLiveDraft() {
        if (this.state.saving || !this.canSaveProviderDraft) return;
        this.state.saving = true; this.state.error = '';
        try { await this.persistLiveDraft(); this.state.notice = 'Draft saved at the email provider.'; }
        catch (error) { this.liveError(error, 'Could not save the provider draft.'); }
        finally { this.state.saving = false; }
    }
    async sendLiveDraft() {
        if (this.state.saving) return;
        const c = this.state.composer;
        if (!c || c.smtpUncertain) return;
        if (!c.to.trim() || !c.subject.trim() || !c.body.trim()) { this.state.error = 'Enter recipient, subject and message.'; return; }
        if (!window.confirm(`Send this email from ${this.accountEmail(c.accountId)} to ${c.to}?`)) return;
        this.state.saving = true; this.state.error = '';
        try {
            let result;
            if (this.accounts.find(a => a.id === c.accountId)?.provider === 'pop3') {
                result = await this.orm.call('mail_client.account', 'send_message', [[c.accountId], {to: c.to, subject: c.subject, body: c.body}, c.replyToId || null]);
            } else {
                const draft = await this.persistLiveDraft();
                await this.orm.call('mail_client.account', 'send_draft', [[draft.accountId], draft.id]);
            }
            this.state.notice = result ? `SMTP accepted ${result.accepted_count} recipient(s); rejected ${result.refused_count}. Delivery is not guaranteed. Do not resend to recipients already accepted.` : 'Provider accepted the send request. Check Sent for confirmation; recipient delivery is not guaranteed.';
            this.state.composer = null; this.state.dirty = false;
            await this.refreshLive();
        } catch (error) {
            if (this.accounts.find(a => a.id === c.accountId)?.provider === 'pop3') {
                c.smtpUncertain = true;
                this.liveError(error, 'SMTP submission could not be confirmed.');
                this.state.error += ' Sending is locked for this message. Verify delivery before closing and composing again.';
            } else this.liveError(error, 'Sending could not be confirmed. Check provider Sent before retrying.');
        }
        finally { this.state.saving = false; }
    }
    async manageAccounts() {
        if (!this.canLeaveComposer()) return;
        await this.action.doAction('mail_client.action_mail_accounts');
    }
    showAccountForm(account = null) {
        if (this.state.saving || !this.canLeaveComposer()) return;
        this.state.composer = null; this.state.dirty = false;
        const isEdit = !!account?.id;
        const provider = account?.provider || 'gmail';
        // For OAuth providers (gmail/outlook), email is optional and comes from OAuth callback
        // For POP3, email is required and user-entered
        this.state.accountForm = {
            id: account?.id || null,
            name: account?.name || '',
            provider: provider,
            email: isEdit ? account?.email || '' : '',
            username: account?.pop3_username || '',
            password: '',
            pop_host: account?.pop3_host || 'pop.gmail.com',
            pop_port: account?.pop3_port || 995,
            smtp_host: account?.smtp_host || 'smtp.gmail.com',
            smtp_port: account?.smtp_port || 465,
            smtp_security: account?.smtp_security || 'ssl'
        };
        this.state.showAccountForm = true; this.state.error = '';
    }
    closeAccountForm() {
        if (this.state.saving) return;
        this.state.accountForm.password = '';
        this.state.showAccountForm = false;
    }
    async saveAccount() {
        if (this.state.saving) return;
        this.state.saving = true; this.state.error = '';
        try {
            const f = this.state.accountForm;
            const name = f.name.trim();
            const isOAuth = f.provider === 'gmail' || f.provider === 'outlook';

            if (!name) {
                this.state.error = 'Enter an account name.'; return;
            }

            if (isOAuth) {
                // OAuth providers: only name required, email comes from OAuth callback
                const newId = await this.orm.call('mail_client.account', 'create', [[{name, provider: f.provider, email_address: ''}]]);
                f.password = ''; this.state.showAccountForm = false;
                await this.loadAccounts();
                // Trigger OAuth flow
                await this.connectAccount(newId);
                return;
            }

            // POP3/SMTP: full validation
            const email = f.email.trim();
            if (!email || !/^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(email)) {
                this.state.error = 'Enter a valid email address.'; return;
            }
            if (!f.password) {
                this.state.error = 'Enter password or app password.'; return;
            }
            const settings = {username: f.username.trim() || email, password: f.password, pop_host: f.pop_host.trim(), pop_port: Number(f.pop_port), smtp_host: f.smtp_host.trim(), smtp_port: Number(f.smtp_port), smtp_security: f.smtp_security};
            if (f.id) await this.orm.call('mail_client.account', 'configure_pop3_account', [[f.id], settings]);
            else await this.orm.call('mail_client.account', 'create_pop3_account', [name, email, settings]);
            f.password = ''; this.state.showAccountForm = false;
            await this.loadAccounts();
            this.state.notice = 'POP3 + SMTP connected. Open Inbox or Compose.';
            await this.refreshLive();
        } catch (error) {
            this.liveError(error, 'Could not save account setup. Check the settings and your access rights.');
        } finally { this.state.saving = false; }
    }
}

registry.category('actions').add('mail_client.client', ManufacturingMailClient);