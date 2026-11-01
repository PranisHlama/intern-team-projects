from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parseaddr
from urllib.parse import quote

from .runtime import http_json, ProviderError
from .formatting import gmail_html, reader_document, text_html, formatted_message, inline_images, referenced_cids, MAX_INLINE_IMAGE
from .content import gmail_body, mime_message, has_attachments, decoded_header


class GmailProvider:
    origin = 'https://gmail.googleapis.com/gmail/v1/users/me'

    def __init__(self, token, email):
        self.token = token; self.email = email

    def request(self, method, path, params=None, payload=None):
        return http_json(method, self.origin + path, headers={'Authorization': 'Bearer ' + self.token}, params=params, payload=payload)

    def identity(self):
        profile = self.request('GET', '/profile')
        return {'id': profile['emailAddress'], 'email': profile['emailAddress']}

    def _message(self, data, body=False):
        headers = {h['name'].lower(): h['value'] for h in data.get('payload', {}).get('headers', [])}
        name, sender = parseaddr(decoded_header(headers.get('from', '')))
        reply = parseaddr(headers.get('reply-to', ''))[1] or sender
        return dict(id=data['id'], senderName=name or sender, sender=sender, replyTo=reply,
                    to=decoded_header(headers.get('to', '')), subject=decoded_header(headers.get('subject', '')),
                    snippet=data.get('snippet', ''), isRead='UNREAD' not in data.get('labelIds', []),
                    date=datetime.fromtimestamp(int(data.get('internalDate', 0)) / 1000, timezone.utc).isoformat(),
                    body=gmail_body(data.get('payload', {})) if body else '',
                    bodyDocument=reader_document(gmail_html(data.get('payload', {})) or text_html(gmail_body(data.get('payload', {})))) if body else '')

    def _get(self, message_id, full=False):
        params = {'format': 'full' if full else 'metadata'}
        if not full: params['metadataHeaders'] = ['From', 'To', 'Subject', 'Reply-To']
        return self.request('GET', '/messages/' + quote(message_id, safe=''), params=params)

    def list_messages(self, *, folder='inbox', cursor=None, limit=25, query='', unread=False):
        params = {'labelIds': 'INBOX' if folder == 'inbox' else 'SENT', 'maxResults': limit}
        search = query.strip() + (' is:unread' if unread else '')
        if search.strip(): params['q'] = search.strip()
        if cursor: params['pageToken'] = cursor
        result = self.request('GET', '/messages', params=params)
        with ThreadPoolExecutor(max_workers=4) as pool:
            items = list(pool.map(lambda row: self._message(self._get(row['id'])), result.get('messages', [])))
        return {'items': items, 'next_cursor': result.get('nextPageToken')}

    def get_message(self, *, message_id):
        data=self._get(message_id, True)
        value=gmail_html(data.get('payload',{})) or text_html(gmail_body(data.get('payload',{})))
        entries=[]
        def visit(part):
            cid=next((h.get('value','') for h in part.get('headers',[]) if h.get('name','').lower()=='content-id'),'')
            if cid and str(part.get('mimeType','')).startswith('image/'):
                entries.append({'cid':cid,'size':part.get('body',{}).get('size',0),'part':part})
            for child in part.get('parts',[]):visit(child)
        if referenced_cids(value):visit(data.get('payload',{}))
        def load(entry):
            from .content import decoded
            body=entry['part'].get('body',{})
            encoded=body.get('data','')
            if body.get('attachmentId'):
                encoded=self.request('GET','/messages/'+quote(message_id,safe='')+'/attachments/'+quote(body['attachmentId'],safe='')).get('data','')
            if len(encoded)>4*((MAX_INLINE_IMAGE+2)//3):return b''
            return decoded(encoded)
        item=self._message(data, True)
        item.update(formatted_message(value,inline_images(value,entries,load)))
        return item

    def _draft_data(self, draft_id, full=False):
        params = {'format': 'full' if full else 'metadata'}
        return self.request('GET', '/drafts/' + quote(draft_id, safe=''), params=params)

    def list_drafts(self, *, cursor=None, limit=25):
        params = {'maxResults': limit}
        if cursor: params['pageToken'] = cursor
        result = self.request('GET', '/drafts', params=params)
        def summary(row):
            draft = self._draft_data(row['id']); item = self._message(draft['message'])
            item.update(id=draft['id'], senderName=item['to'], isRead=True)
            return item
        with ThreadPoolExecutor(max_workers=4) as pool:
            items = list(pool.map(summary, result.get('drafts', [])))
        return {'items': items, 'next_cursor': result.get('nextPageToken')}

    def get_draft(self, *, draft_id):
        result = self._draft_data(draft_id, True)
        item = self._message(result['message'], True); item['id'] = result['id']
        return item

    def save_draft(self, *, draft, draft_id=None, reply_to_id=None):
        headers, thread = {}, None
        if draft_id:
            existing = self._draft_data(draft_id, True)['message']
            if has_attachments(existing.get('payload', {})):
                raise ProviderError('Editing Gmail drafts with attachments is not supported yet. Edit this draft in Gmail to preserve its attachments.')
            previous = {h['name'].lower(): h['value'] for h in existing.get('payload', {}).get('headers', [])}
            headers = {k: previous.get(k.lower()) for k in ('In-Reply-To', 'References')}
            thread = existing.get('threadId')
        elif reply_to_id:
            original = self._get(reply_to_id, True)
            values = {h['name'].lower(): h['value'] for h in original.get('payload', {}).get('headers', [])}
            message_id = values.get('message-id')
            if not message_id: raise ProviderError('The original message has no Message-ID; use Gmail to reply.')
            headers = {'In-Reply-To': message_id, 'References': (values.get('references', '') + ' ' + message_id).strip()}
            thread = original['threadId']
        message = {'raw': mime_message(self.email, draft, headers)}
        if thread: message['threadId'] = thread
        path = '/drafts' + ('/' + quote(draft_id, safe='') if draft_id else '')
        result = self.request('PUT' if draft_id else 'POST', path, payload={'message': message})
        return {'id': result['id']}

    def send_draft(self, *, draft_id):
        result = self.request('POST', '/drafts/send', payload={'id': draft_id})
        return {'id': result['id'], 'sent': True}

    def reply(self, *, message_id, draft):
        return self.save_draft(draft=draft, reply_to_id=message_id)

    def set_read(self, *, message_id, is_read):
        key = 'removeLabelIds' if is_read else 'addLabelIds'
        self.request('POST', '/messages/' + quote(message_id, safe='') + '/modify', payload={key: ['UNREAD']})
        return True

    def get_attachment(self, **kwargs):
        raise ProviderError('Attachment downloads are deferred to the next phase.')
