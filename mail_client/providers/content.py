"""Plain-text presentation only; never execute email HTML or fetch remote images."""
import base64
from email.message import EmailMessage
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser

from .runtime import ProviderError


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []; self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1
        if not self.hidden and tag in ('p', 'br', 'div', 'li', 'tr'): self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def plain_html(value):
    parser = PlainHTML(); parser.feed(value or '')
    return ''.join(parser.parts).strip()


def recipients(value):
    if '\r' in value or '\n' in value:
        raise ProviderError('Recipient addresses cannot contain line breaks.')
    parsed = getaddresses([value])
    if not parsed or any(not address or '@' not in address or any(c.isspace() for c in address) for _, address in parsed):
        raise ProviderError('Enter valid email recipients, separated by commas.')
    return [{'emailAddress': {'address': address, 'name': name}} for name, address in parsed]


def mime_message(sender, draft, headers=None):
    if draft['to'].strip(): recipients(draft['to'])
    if '\r' in draft['subject'] or '\n' in draft['subject']:
        raise ProviderError('The subject cannot contain line breaks.')
    message = EmailMessage()
    message['From'] = sender; message['To'] = draft['to']; message['Subject'] = draft['subject']
    for key, value in (headers or {}).items():
        if value: message[key] = value
    message.set_content(draft['body'])
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def decoded(value):
    try:
        return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    except (ValueError, TypeError):
        raise ProviderError('Provider returned malformed message content.') from None


def gmail_body(payload):
    plain, html = [], []
    def visit(part):
        if part.get('filename'): return
        data = part.get('body', {}).get('data')
        if data:
            charset = 'utf-8'
            content_type = next((h['value'] for h in part.get('headers', []) if h['name'].lower() == 'content-type'), '')
            import re
            match = re.search(r'charset=["\']?([^;"\'\s]+)', content_type, re.I)
            if match: charset = match.group(1)
            try: text = decoded(data).decode(charset, errors='replace')
            except LookupError: text = decoded(data).decode('utf-8', errors='replace')
            if part.get('mimeType') == 'text/plain': plain.append(text)
            elif part.get('mimeType') == 'text/html': html.append(plain_html(text))
        for child in part.get('parts', []): visit(child)
    visit(payload)
    return '\n'.join(plain or html)


def has_attachments(payload):
    return bool(payload.get('filename')) or any(has_attachments(p) for p in payload.get('parts', []))


def decoded_header(value):
    try:
        return str(make_header(decode_header(value or '')))
    except (ValueError, LookupError, TypeError):
        return value or ''
