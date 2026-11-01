"""Conservative email HTML allowlist and isolated-reader document."""
import html
import base64
import binascii
import ipaddress
from urllib.parse import urlparse, unquote
import re
from html.parser import HTMLParser

TAGS = set('p div span br hr strong b em i u s blockquote pre code ul ol li table thead tbody tfoot tr td th h1 h2 h3 h4 h5 h6 a center'.split())
VOID = {'br','hr'}
HIDDEN = {'script','style','iframe','object','embed','svg','math','template','noscript','head'}
STYLES = {'color','background-color','font-size','font-weight','font-style','font-family','text-align','text-decoration','line-height','border','border-collapse','padding','margin','width','max-width','vertical-align','white-space'}

class SafeEmailHTML(HTMLParser):
    def __init__(self, images=None, external=False):
        self.images = images or {}
        self.external = external
        self.has_external = False
        super().__init__(convert_charrefs=True)
        self.parts=[];self.hidden=[];self.stack=[]
    def handle_starttag(self, tag, attrs):
        if tag in HIDDEN:
            self.hidden.append(tag);return
        if self.hidden:return
        if tag=='img':
            values = dict(attrs)
            source = (values.get('src') or '').strip()
            remote = external_image_url(source)
            if remote:
                self.has_external = True
                source = remote if self.external else ''
            elif source.lower().startswith('cid:'):
                source = self.images.get(normalize_cid(source[4:]), '')
            elif source.lower().startswith('data:'):
                source = checked_data_image(source)
            else:
                source = ''
            alt = (values.get('alt') or 'Email image')[:200]
            if source:
                size = ''.join(' '+key+'="'+value+'"' for key in ('width','height')
                               if len(value := values.get(key, '')) <= 4 and value.isdigit() and 1 <= int(value) <= 2000)
                self.parts.append('<img src="'+html.escape(source,quote=True)+'" alt="'+html.escape(alt,quote=True)+'" referrerpolicy="no-referrer"'+size+'>')
            elif not remote:
                self.parts.append('<span class="image-note">['+html.escape(alt)+' unavailable]</span>')
            return
        if tag not in TAGS:return
        clean=[]
        for key,value in attrs:
            if not value:continue
            if tag=='a' and key=='href' and re.match(r'^(https?://|mailto:)',value,re.I):
                clean += [('href',value),('target','_blank'),('rel','noopener noreferrer')]
            elif key in ('colspan','rowspan') and value.isdigit() and int(value)<=50:
                clean.append((key,value))
            elif key=='style':
                styles=[]
                for entry in value.split(';'):
                    name,sep,v=entry.partition(':');name=name.strip().lower();v=v.strip()
                    if sep and name in STYLES and len(v)<150 and re.fullmatch(r'[a-zA-Z0-9#.,% ()"\'\-]+',v) and not re.search(r'url|expression|import|javascript|var\(',v,re.I):
                        styles.append(name+':'+v)
                if styles:clean.append(('style',';'.join(styles)))
        self.parts.append('<'+tag+''.join(' '+k+'="'+html.escape(v,quote=True)+'"' for k,v in clean)+'>')
        if tag not in VOID:self.stack.append(tag)
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in VOID:self.handle_endtag(tag)
    def handle_endtag(self,tag):
        if self.hidden:
            if tag==self.hidden[-1]:self.hidden.pop()
            return
        if tag in self.stack:
            while self.stack:
                current=self.stack.pop();self.parts.append('</'+current+'>')
                if current==tag:break
    def handle_data(self,value):
        if not self.hidden:self.parts.append(html.escape(value))

def sanitize_html(value, images=None, external=False):
    parser=SafeEmailHTML(images, external);parser.feed((value or '')[:400000]);parser.close()
    return ''.join(parser.parts)+''.join('</'+tag+'>' for tag in reversed(parser.stack))

def text_html(value):
    return '<div style="white-space:pre-wrap">'+html.escape(value or '').replace('\n','<br>')+'</div>'

def reader_document(value, images=None, external=False):
    body=sanitize_html(value, images, external)
    image_policy = "data: https:" if external else "data:"
    return '''<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src IMAGE_POLICY; base-uri 'none'; form-action 'none'"><meta name="referrer" content="no-referrer"><style>body{font:15px/1.65 Arial,sans-serif;color:#242833;margin:20px;overflow-wrap:anywhere}table{border-collapse:collapse;max-width:100%}td,th{padding:6px;vertical-align:top}pre{white-space:pre-wrap}blockquote{border-left:3px solid #c9bfdc;margin:16px 0;padding:0 16px;color:#606572}a{color:#716096}img{max-width:100%;height:auto;vertical-align:middle}.image-note{color:#777;font-size:12px}h1,h2,h3{line-height:1.3}p{margin:0 0 12px}</style></head><body>'''.replace('IMAGE_POLICY',image_policy)+body+'</body></html>'


def gmail_html(payload):
    """Read HTML body parts without interpreting attachment files as message content."""
    from .content import decoded
    if payload.get('filename'):
        return ''
    if payload.get('mimeType') == 'text/html':
        data = payload.get('body', {}).get('data')
        if data:
            header = next((h.get('value', '') for h in payload.get('headers', [])
                           if h.get('name', '').lower() == 'content-type'), '')
            match = re.search(r'charset=["\']?([^;"\'\s]+)', header, re.I)
            charset = match.group(1) if match else 'utf-8'
            raw = decoded(data)
            try:
                return raw.decode(charset, errors='replace')
            except LookupError:
                return raw.decode('utf-8', errors='replace')
    return ''.join(gmail_html(part) for part in payload.get('parts', []))


MAX_INLINE_IMAGE = 512 * 1024
MAX_INLINE_TOTAL = 2 * 1024 * 1024
MAX_INLINE_COUNT = 10

def normalize_cid(value):
    return unquote(str(value or '')).strip().strip('<>')

def external_image_url(value):
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or '').lower()
        if parsed.scheme.lower() != 'https' or not host or parsed.username or parsed.password:
            return ''
        if any(ord(c) < 33 for c in value) or len(value) > 8192:
            return ''
        if host == 'localhost' or host.endswith(('.localhost','.local','.internal')):
            return ''
        try:
            if not ipaddress.ip_address(host).is_global:
                return ''
        except ValueError:
            if '.' not in host:
                return ''
        return value
    except (ValueError,TypeError):
        return ''

def image_uri(raw):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_INLINE_IMAGE:
        return ''
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        kind='png'
    elif raw.startswith(b'\xff\xd8\xff'):
        kind='jpeg'
    elif raw.startswith((b'GIF87a',b'GIF89a')):
        kind='gif'
    elif raw.startswith(b'RIFF') and raw[8:12]==b'WEBP':
        kind='webp'
    else:
        return ''
    return 'data:image/'+kind+';base64,'+base64.b64encode(raw).decode()

def checked_data_image(value):
    try:
        header, encoded = value.split(',',1)
        if header.lower() not in ('data:image/png;base64','data:image/jpeg;base64','data:image/gif;base64','data:image/webp;base64') or len(encoded)>4*((MAX_INLINE_IMAGE+2)//3):
            return ''
        return image_uri(base64.b64decode(encoded,validate=True))
    except (ValueError,binascii.Error):
        return ''

class ReferencedImages(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cids=set()
    def handle_starttag(self,tag,attrs):
        if tag=='img':
            value=dict(attrs).get('src','')
            if value.lower().startswith('cid:') and len(self.cids)<MAX_INLINE_COUNT:
                self.cids.add(normalize_cid(value[4:]))

def referenced_cids(value):
    parser=ReferencedImages();parser.feed((value or '')[:400000]);return parser.cids

def inline_images(value, entries, load):
    """Resolve only bounded, referenced images; never fetch an external URL on the server."""
    from .runtime import ProviderError
    wanted=referenced_cids(value)
    result={};total=0;attempted=set()
    if not wanted:
        return result
    for entry in entries:
        cid=normalize_cid(entry.get('cid'))
        if cid not in wanted or cid in attempted or len(attempted)>=MAX_INLINE_COUNT:
            continue
        attempted.add(cid)
        size=entry.get('size',0)
        if not isinstance(size,int) or size<0 or size>MAX_INLINE_IMAGE or total+size>MAX_INLINE_TOTAL:
            continue
        try:
            raw=load(entry)
            if total+len(raw)>MAX_INLINE_TOTAL:
                continue
            uri=image_uri(raw)
            if uri:
                result[cid]=uri;total+=len(raw)
        except (ProviderError,ValueError,KeyError,TypeError,binascii.Error):
            # Missing/unsupported images must not hide the message text.
            continue
    return result

def formatted_message(value, images=None):
    parser=SafeEmailHTML(images);parser.feed((value or '')[:400000]);parser.close()
    return {'bodyDocument':reader_document(value,images),
            'bodyDocumentExternal':reader_document(value,images,True) if parser.has_external else '',
            'hasExternalImages':parser.has_external}
