"""Receive-only POP3 over verified TLS. No DELE, persistent mail or SMTP."""
import base64
from contextlib import contextmanager
from datetime import timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime, parseaddr
import json
import poplib
import re
import ssl
import time

from cryptography.fernet import Fernet, InvalidToken

from .content import decoded_header, plain_html
from .runtime import ProviderError, server_configuration
from .smtp import SMTPProvider
from .formatting import reader_document, text_html, formatted_message, inline_images

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_MAILBOX_MESSAGES = 5000
CAPABILITIES = dict(inbox=True, sent=False, drafts=False, send=False,
                    reply=False, search=False, read_state=False)


def pop3_configuration(email, database, owner_id, company_id):
    """Only an administrator can provision server destinations and owner bindings."""
    config = server_configuration()
    try:
        profiles = config.get('pop3', {}).get('accounts', [])
        matches = [p for p in profiles if
                   (p['database'], p['owner_id'], p['company_id'], p['email']) ==
                   (database, owner_id, company_id, email)]
        if len(matches) != 1:
            raise ProviderError('POP3 setup is missing for this user, company and database. Ask the administrator to run configure_pop3.py.')
        profile = matches[0]
        host, username, port = profile['host'], profile['username'], profile['port']
        if (not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]{0,252}', host)
                or not isinstance(username, str) or not username or len(username) > 254
                or '\r' in username or '\n' in username
                or type(port) is not int or not 1 <= port <= 65535):
            raise ValueError('profile')
        password = Fernet(config['encryption_key'].encode()).decrypt(profile['password_encrypted'].encode()).decode()
        if not password or '\r' in password or '\n' in password or len(password) > 1024:
            raise ValueError('password')
        return {'host': host, 'port': port, 'username': username, 'password': password}
    except ProviderError:
        raise
    except (KeyError, ValueError, TypeError, AttributeError, InvalidToken):
        raise ProviderError('POP3 configuration is invalid. Ask the administrator to check POP3_SETUP.md.') from None


class BoundedPOP3SSL(poplib.POP3_SSL):
    """Limit multiline replies while reading, before they accumulate in memory."""
    response_limit = MAX_MESSAGE_BYTES

    def __init__(self, *args, **kwargs):
        self.deadline = time.monotonic() + 30
        super().__init__(*args, **kwargs)

    def _getline(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError('POP3 retrieval took too long. Try a smaller mailbox or contact your administrator.')
        self.sock.settimeout(min(15, remaining))
        return super()._getline()

    def _getlongresp(self):
        response = self._getresp()
        lines, size = [], 0
        while True:
            line, octets = self._getline()
            if line == b'.':
                return response, lines, size
            size += octets
            if size > self.response_limit:
                raise ProviderError('POP3 response is too large. Open this message in another mail client.')
            if line.startswith(b'..'):
                line = line[1:]
            lines.append(line)


class POP3Provider:
    capabilities = CAPABILITIES

    def __init__(self, config, email=''):
        self.config, self.email = config, email
        self.smtp = SMTPProvider(config['smtp'], email) if config.get('smtp') else None

    @contextmanager
    def _session(self):
        client = None
        completed = False
        try:
            client = BoundedPOP3SSL(self.config['host'], self.config['port'],
                                    timeout=15, context=ssl.create_default_context())
            client.user(self.config['username'])
            client.pass_(self.config['password'])
            yield client
            completed = True
        except ProviderError:
            raise
        except (OSError, poplib.error_proto, ValueError, UnicodeError):
            # Never expose a server response, username or password through RPC.
            raise ProviderError('POP3 connection failed. Check the server, TLS certificate and account credentials with your administrator.') from None
        finally:
            if client is not None:
                if not completed:
                    client.close()
                else:
                    try:
                        # No DELE is ever issued. Server retention policies still apply.
                        client.quit()
                    except (OSError, poplib.error_proto, ProviderError):
                        client.close()

    @staticmethod
    def _maildrop(client):
        count, _ = client.stat()
        if count > MAX_MAILBOX_MESSAGES:
            raise ProviderError('This POP3 mailbox exceeds the initial 5,000-message limit.')
        if not count:
            return {}
        # UIDL is optional in POP3; require it rather than use unstable IDs.
        try:
            _, lines, _ = client.uidl()
        except poplib.error_proto:
            raise ProviderError('This POP3 server must support UIDL for stable message identifiers.') from None
        mapping = {}
        try:
            for line in lines:
                number, uid = line.decode('ascii').split()
                number = int(number)
                if not 1 <= number <= count or number in mapping or not re.fullmatch(r'[\x21-\x7e]{1,70}', uid):
                    raise ValueError('uid')
                mapping[number] = uid
            if set(mapping) != set(range(1, count + 1)):
                raise ValueError('incomplete uidl')
        except (ValueError, UnicodeError):
            raise ProviderError('The POP3 server returned invalid message identifiers.') from None
        return mapping

    @staticmethod
    def _encode(value):
        return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode()

    @staticmethod
    def _decode(value):
        try:
            if not isinstance(value, str) or len(value) > 2048:
                raise ValueError('id')
            return json.loads(base64.b64decode(value, altchars=b'-_', validate=True))
        except (ValueError, TypeError, UnicodeError):
            raise ProviderError('Invalid POP3 identifier. Refresh the inbox.') from None

    def _number(self, message_id, mapping):
        identity = self._decode(message_id)
        if not isinstance(identity, dict) or set(identity) != {'number', 'uid'} or type(identity['number']) is not int:
            raise ProviderError('Invalid POP3 identifier. Refresh the inbox.')
        if mapping.get(identity['number']) == identity['uid']:
            return identity['number']
        candidates = [n for n, uid in mapping.items() if uid == identity['uid']]
        if len(candidates) == 1:
            return candidates[0]
        raise ProviderError('This POP3 message changed or is no longer available. Refresh the inbox.')

    @staticmethod
    def _retrieve(client, number):
        try:
            size = int(client.list(number).decode('ascii').split()[2])
        except (ValueError, IndexError, UnicodeError):
            raise ProviderError('The POP3 server returned an invalid message size.') from None
        if not 0 <= size <= MAX_MESSAGE_BYTES:
            raise ProviderError('This POP3 message exceeds the 8 MiB limit. Open it in another mail client.')
        return client.retr(number)

    @staticmethod
    def _summary(raw, message_id):
        message = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
        name, sender = parseaddr(decoded_header(str(message.get('From', ''))))
        date = ''
        try:
            parsed = parsedate_to_datetime(str(message.get('Date', '')))
            if parsed:
                date = (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat()
        except (ValueError, TypeError, OverflowError):
            pass
        return {'id': message_id, 'senderName': name or sender or 'Unknown sender',
                'sender': sender, 'replyTo': parseaddr(str(message.get('Reply-To', '')))[1] or sender,
                'to': decoded_header(str(message.get('To', ''))),
                'subject': decoded_header(str(message.get('Subject', ''))),
                'date': date, 'snippet': '', 'isRead': True}

    def check_connection(self):
        with self._session() as client:
            self._maildrop(client)
        return True

    def list_messages(self, folder='inbox', cursor=None, limit=25, query='', unread=False):
        if folder != 'inbox' or query or unread:
            raise ProviderError('POP3 supports inbox retrieval only; provider search and read/unread synchronization are unavailable.')
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ProviderError('Page size must be between 1 and 50.')
        with self._session() as client:
            mapping = self._maildrop(client)
            if not mapping:
                if cursor:
                    raise ProviderError('The POP3 mailbox changed. Refresh the inbox.')
                return {'items': [], 'next_cursor': None}
            last = max(mapping)
            start, anchor_number, anchor_uid = last, last, mapping[last]
            if cursor:
                data = self._decode(cursor)
                if (not isinstance(data, dict) or set(data) != {'next', 'anchor_number', 'anchor_uid'}
                        or type(data['next']) is not int or type(data['anchor_number']) is not int
                        or not 1 <= data['next'] <= data['anchor_number'] <= last
                        or mapping.get(data['anchor_number']) != data['anchor_uid']):
                    raise ProviderError('The POP3 mailbox changed. Refresh the inbox.')
                start, anchor_number, anchor_uid = data['next'], data['anchor_number'], data['anchor_uid']
            items = []
            stop = max(0, start - limit)
            for number in range(start, stop, -1):
                try:
                    client.response_limit = 65536
                    result = client.top(number, 0)
                except poplib.error_proto:
                    # TOP is optional. Fallback downloads only the selected page's
                    # messages, bounded by size, and discards bodies immediately.
                    client.response_limit = MAX_MESSAGE_BYTES
                    result = self._retrieve(client, number)
                finally:
                    client.response_limit = MAX_MESSAGE_BYTES
                message_id = self._encode({'number': number, 'uid': mapping[number]})
                items.append(self._summary(b'\r\n'.join(result[1]), message_id))
            cursor = self._encode({'next': stop, 'anchor_number': anchor_number,
                                   'anchor_uid': anchor_uid}) if stop else None
            return {'items': items, 'next_cursor': cursor}

    def get_message(self, message_id):
        with self._session() as client:
            number = self._number(message_id, self._maildrop(client))
            raw = b'\r\n'.join(self._retrieve(client, number)[1])
        result = self._summary(raw, message_id)
        message = BytesParser(policy=policy.default).parsebytes(raw)
        plain, html = [], []
        attachments = False
        def visit(part):
            nonlocal attachments
            if part.get_content_disposition() == 'attachment' or part.get_filename():
                attachments = True
                return
            if part.is_multipart():
                for child in part.iter_parts():
                    visit(child)
                return
            if part.get_content_type() not in ('text/plain', 'text/html'):
                return
            value = part.get_payload(decode=True) or b''
            try:
                text = value.decode(part.get_content_charset() or 'utf-8', errors='replace')
            except LookupError:
                text = value.decode('utf-8', errors='replace')
            (plain if part.get_content_type() == 'text/plain' else html).append(text)
        visit(message)
        result.update(body='\n'.join(plain or [plain_html(value) for value in html]), hasAttachments=attachments,
                      bodyDocument=reader_document(''.join(html) or text_html('\n'.join(plain))))
        value=''.join(html) or text_html('\n'.join(plain))
        entries=[{'cid':str(part.get('Content-ID','')),'size':len(part.get_payload(decode=True) or b''),'part':part}
                 for part in message.walk() if not part.is_multipart() and part.get_content_maintype()=='image']
        item_images=inline_images(value,entries,lambda entry:entry['part'].get_payload(decode=True) or b'')
        result.update(formatted_message(value,item_images))
        for key, field in [('Message-ID', 'messageId'), ('References', 'references')]:
            value = str(message.get(key, ''))
            result[field] = value if len(value) <= 2048 and '\r' not in value and '\n' not in value else ''
        return result

    def send_message(self, draft, reply_to_id=None):
        if not self.smtp:
            raise ProviderError('SMTP settings are missing. Open Server settings for this account.')
        headers = {}
        if reply_to_id:
            original = self.get_message(reply_to_id)
            if original.get('messageId'):
                headers['In-Reply-To'] = original['messageId']
                refs = (original.get('references', '') + ' ' + original['messageId']).strip()
                headers['References'] = refs[-2048:] if len(refs) <= 2048 else original['messageId']
        return self.smtp.send_message(draft, headers=headers)
