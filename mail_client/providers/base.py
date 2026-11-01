"""Live adapters return transient dictionaries. They never write mail-content ORM records."""
from .runtime import ProviderError
from .gmail import GmailProvider
from .outlook import OutlookProvider
from .pop3 import POP3Provider, CAPABILITIES as POP3_CAPABILITIES

ProviderNotReady = ProviderError  # compatibility with the foundation model


def provider_capabilities(provider, smtp_enabled=False):
    if provider == 'pop3':
        return dict(POP3_CAPABILITIES, send=smtp_enabled, reply=smtp_enabled)
    return dict(inbox=True, sent=True, drafts=True, send=True, reply=True, search=True, read_state=True)


def get_provider(provider, token=None, email='', pop3_config=None):
    if provider == 'pop3':
        if pop3_config is None:
            raise ProviderError('POP3 account configuration is missing.')
        return POP3Provider(pop3_config, email)
    if not token:
        raise ProviderError('This account is not connected. Connect it with OAuth first.')
    if provider == 'gmail': return GmailProvider(token, email)
    if provider == 'outlook': return OutlookProvider(token, email)
    raise ValueError('Unsupported provider')
