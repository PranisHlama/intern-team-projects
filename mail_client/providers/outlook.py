from urllib.parse import quote, urlparse
from .runtime import http_json, ProviderError
from .content import plain_html, recipients
from .formatting import reader_document, text_html, formatted_message, referenced_cids, inline_images, MAX_INLINE_IMAGE


class OutlookProvider:
    origin = 'https://graph.microsoft.com/v1.0'
    select = 'id,subject,from,toRecipients,replyTo,receivedDateTime,lastModifiedDateTime,bodyPreview,isRead,isDraft'

    def __init__(self, token, email):
        self.token = token; self.email = email

    def request(self, method, path, params=None, payload=None, *, formatted_body=False):
        return http_json(method, self.origin + path, headers={'Authorization': 'Bearer ' + self.token,
                         'Prefer': ('outlook.body-content-type="html", IdType="ImmutableId"' if formatted_body else 'outlook.body-content-type="text", IdType="ImmutableId"')}, params=params, payload=payload)

    def identity(self):
        result = self.request('GET', '/me', params={'$select': 'id,mail,userPrincipalName'})
        return {'id': result['id'], 'email': result.get('mail') or result.get('userPrincipalName', '')}

    def _message(self, data, body=False):
        sender = data.get('from', {}).get('emailAddress', {})
        reply = (data.get('replyTo') or [{'emailAddress': sender}])[0]['emailAddress'].get('address', '')
        content = data.get('body', {})
        return dict(id=data['id'], senderName=sender.get('name') or sender.get('address', ''), sender=sender.get('address', ''),
                    replyTo=reply, to=', '.join(r['emailAddress']['address'] for r in data.get('toRecipients', [])),
                    subject=data.get('subject', ''), snippet=data.get('bodyPreview', ''), isRead=data.get('isRead', True),
                    date=(data.get('lastModifiedDateTime') if data.get('isDraft') else data.get('receivedDateTime')) or data.get('lastModifiedDateTime'),
                    bodyDocument=reader_document(content.get('content', '') if content.get('contentType', '').lower() == 'html' else text_html(content.get('content', ''))) if body else '',
                    body=(plain_html(content.get('content', '')) if content.get('contentType', '').lower() == 'html' else content.get('content', '')) if body else '')

    def _page(self, path, params, cursor):
        if cursor:
            parsed = urlparse(cursor)
            # Never attach a bearer token to an arbitrary client-supplied nextLink.
            if parsed.scheme != 'https' or parsed.netloc != 'graph.microsoft.com' or parsed.path != '/v1.0' + path or parsed.fragment:
                raise ProviderError('Invalid Microsoft pagination cursor. Refresh the mailbox.')
            result = http_json('GET', cursor, headers={'Authorization': 'Bearer ' + self.token, 'Prefer': 'IdType="ImmutableId"'})
        else:
            result = self.request('GET', path, params=params)
        return {'items': [self._message(m) for m in result.get('value', [])], 'next_cursor': result.get('@odata.nextLink')}

    def list_messages(self, *, folder='inbox', cursor=None, limit=25, query='', unread=False):
        path = '/me/mailFolders/' + ('inbox' if folder == 'inbox' else 'sentitems') + '/messages'
        params = {'$top': limit, '$select': self.select}
        if query.strip():
            # Graph sorts search results by sentDateTime. Explicit $orderby is not supported with $search.
            params['$search'] = '"' + query.replace('"', '').replace('\\', '') + '"'
            if unread:
                raise ProviderError('Use either Search or Unread with Outlook in this MVP. Clear Search before filtering unread.')
        else:
            if unread:
                params['$filter'] = 'receivedDateTime ge 1970-01-01T00:00:00Z and isRead eq false'
                params['$orderby'] = 'receivedDateTime desc'
            else:
                params['$orderby'] = 'receivedDateTime desc'
        return self._page(path, params, cursor)

    def get_message(self, *, message_id):
        data = self.request('GET', '/me/messages/' + quote(message_id, safe=''), params={'$select': self.select + ',body'}, formatted_body=True)
        content=data.get('body',{})
        value=content.get('content','') if content.get('contentType','').lower()=='html' else text_html(content.get('content',''))
        entries=[]
        wanted=referenced_cids(value)
        if wanted:
            # Read small metadata first; request bytes only for bounded inline images.
            try:
                result=self.request('GET','/me/messages/'+quote(message_id,safe='')+'/attachments',
                                    params={'$select':'id,contentType,size,isInline','$top':50})
                candidates=[a for a in result.get('value',[]) if a.get('isInline') and str(a.get('contentType','')).startswith('image/') and isinstance(a.get('size'),int) and 0 <= a['size'] <= MAX_INLINE_IMAGE][:10]
                total=0
                import base64
                from .formatting import normalize_cid, MAX_INLINE_TOTAL
                for candidate in candidates:
                    if total+candidate['size']>MAX_INLINE_TOTAL:continue
                    try:
                        image=self.request('GET','/me/messages/'+quote(message_id,safe='')+'/attachments/'+quote(candidate['id'],safe=''))
                        cid=normalize_cid(image.get('contentId',''))
                        encoded=image.get('contentBytes','')
                        if cid not in wanted or len(encoded)>4*((MAX_INLINE_IMAGE+2)//3):continue
                        raw=base64.b64decode(encoded,validate=True)
                        if total+len(raw)>MAX_INLINE_TOTAL:continue
                        entries.append({'cid':cid,'size':len(raw),'raw':raw});total+=len(raw)
                    except (ProviderError,ValueError,TypeError):
                        continue
            except ProviderError:
                entries=[]
        def load(entry):
            return entry['raw']
        item=self._message(data,True)
        item.update(formatted_message(value,inline_images(value,entries,load)))
        return item

    def list_drafts(self, *, cursor=None, limit=25):
        result = self._page('/me/mailFolders/drafts/messages', {'$top': limit, '$select': self.select, '$orderby': 'lastModifiedDateTime desc'}, cursor)
        for item in result['items']: item['senderName'] = item['to']; item['isRead'] = True
        return result

    def get_draft(self, *, draft_id):
        data = self.request('GET', '/me/messages/' + quote(draft_id, safe=''), params={'$select': self.select + ',body'})
        if not data.get('isDraft'): raise ProviderError('This message is not a draft.')
        return self._message(data, True)

    def save_draft(self, *, draft, draft_id=None, reply_to_id=None):
        payload = {'subject': draft['subject'], 'toRecipients': recipients(draft['to']) if draft['to'].strip() else [],
                   'body': {'contentType': 'Text', 'content': draft['body']}}
        if draft_id:
            self.get_draft(draft_id=draft_id)
        elif reply_to_id:
            result = self.request('POST', '/me/messages/' + quote(reply_to_id, safe='') + '/createReply', payload={})
            draft_id = result['id']
        path = '/me/messages' + ('/' + quote(draft_id, safe='') if draft_id else '')
        result = self.request('PATCH' if draft_id else 'POST', path, payload=payload)
        return {'id': result['id']}

    def send_draft(self, *, draft_id):
        self.get_draft(draft_id=draft_id)
        self.request('POST', '/me/messages/' + quote(draft_id, safe='') + '/send', payload={})
        return {'sent': True, 'accepted': True}

    def reply(self, *, message_id, draft):
        return self.save_draft(draft=draft, reply_to_id=message_id)

    def set_read(self, *, message_id, is_read):
        self.request('PATCH', '/me/messages/' + quote(message_id, safe=''), payload={'isRead': is_read})
        return True

    def get_attachment(self, **kwargs):
        raise ProviderError('Attachment downloads are deferred to the next phase.')
