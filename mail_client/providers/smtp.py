"""Authenticated TLS SMTP submission. No retries, mailbox imports or provider drafts."""
from contextlib import contextmanager
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import re
import smtplib
import ssl

from .content import recipients
from .runtime import ProviderError


def validate_mail_settings(settings, config):
    keys = {'username', 'password', 'pop_host', 'pop_port', 'smtp_host', 'smtp_port', 'smtp_security'}
    if not isinstance(settings, dict) or set(settings) != keys:
        raise ProviderError('Enter all POP3 and SMTP settings.')
    result = dict(settings)
    for key in ('pop_host', 'smtp_host'):
        if not isinstance(result[key], str):
            raise ProviderError('Invalid mail-server hostname.')
        result[key] = result[key].strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', result[key]):
            raise ProviderError('Enter a hostname without a URL, path or credentials.')
    for key in ('pop_port', 'smtp_port'):
        if type(result[key]) is not int or not 1 <= result[key] <= 65535:
            raise ProviderError('Mail-server ports must be integers between 1 and 65535.')
    for key, maximum in (('username', 254), ('password', 1024)):
        value = result[key]
        if not isinstance(value, str) or not value or len(value) > maximum or '\r' in value or '\n' in value:
            raise ProviderError('Enter a valid mail-server username and password/app password.')
    if result['smtp_security'] not in ('ssl', 'starttls'):
        raise ProviderError('SMTP requires implicit TLS or STARTTLS.')
    return result


def pop_config(settings):
    return {'host': settings['pop_host'], 'port': settings['pop_port'],
            'username': settings['username'], 'password': settings['password'],
            'smtp': {'host': settings['smtp_host'], 'port': settings['smtp_port'],
                     'security': settings['smtp_security'], 'username': settings['username'],
                     'password': settings['password']}}


class SMTPProvider:
    def __init__(self, config, email):
        self.config, self.email = config, email

    @contextmanager
    def _session(self):
        client = None
        try:
            context = ssl.create_default_context()
            if self.config['security'] == 'ssl':
                client = smtplib.SMTP_SSL(self.config['host'], self.config['port'], timeout=15, context=context)
            elif self.config['security'] == 'starttls':
                client = smtplib.SMTP(self.config['host'], self.config['port'], timeout=15)
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            else:
                raise ProviderError('SMTP requires TLS.')
            client.login(self.config['username'], self.config['password'])
            yield client
        except ProviderError:
            raise
        except smtplib.SMTPAuthenticationError:
            raise ProviderError('SMTP authentication failed. Check the username and password/app password.') from None
        except smtplib.SMTPRecipientsRefused:
            raise ProviderError('SMTP rejected all recipients. No recipients were accepted.') from None
        except (OSError, smtplib.SMTPException, ValueError, UnicodeError):
            raise ProviderError('SMTP submission could not be confirmed. No automatic retry was made. Verify delivery before sending again.') from None
        finally:
            if client is not None:
                # Closing the socket avoids reporting a QUIT failure as a failed
                # submission after the SMTP server already accepted DATA.
                client.close()

    def check_connection(self):
        with self._session():
            pass
        return True

    def send_message(self, draft, headers=None):
        if not isinstance(draft, dict) or set(draft) != {'to', 'subject', 'body'}:
            raise ProviderError('Invalid message fields.')
        for key, maximum in (('to', 4096), ('subject', 998), ('body', 200000)):
            if not isinstance(draft[key], str) or not draft[key].strip() or len(draft[key]) > maximum:
                raise ProviderError('Enter recipient, subject and message within the supported limits.')
        if '\r' in draft['subject'] or '\n' in draft['subject']:
            raise ProviderError('The subject cannot contain line breaks.')
        addresses = [p['emailAddress']['address'] for p in recipients(draft['to'])]
        if len(addresses) > 50:
            raise ProviderError('A message may have at most 50 recipients.')
        message = EmailMessage()
        message['From'] = self.email
        message['To'] = draft['to']
        message['Subject'] = draft['subject']
        message['Date'] = formatdate(localtime=False)
        message['Message-ID'] = make_msgid()
        for key, value in (headers or {}).items():
            if key not in ('In-Reply-To', 'References') or not isinstance(value, str) or len(value) > 2048 or '\n' in value or '\r' in value:
                raise ProviderError('Invalid reply headers.')
            if value:
                message[key] = value
        message.set_content(draft['body'])
        with self._session() as client:
            refused = client.send_message(message, from_addr=self.email, to_addrs=addresses)
        return {'accepted': True, 'accepted_count': len(addresses)-len(refused),
                'refused_count': len(refused), 'message_id': str(message['Message-ID'])}