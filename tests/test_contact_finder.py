"""Unit tests for public-data e-mail discovery."""
from app.services.contact_finder import find_contacts


def test_general_mailboxes_only_on_own_domain():
    pages = {
        'https://example.nl/contact': (
            '<html><body>'
            '<a href="mailto:info@example.nl">contact</a>'
            ' sales@example.nl, administratie@example.nl'
            '</body></html>'
        )
    }
    found = find_contacts(pages, 'https://example.nl')
    assert {x['email'] for x in found} == {'info@example.nl', 'sales@example.nl', 'administratie@example.nl'}


def test_personal_looking_address_on_own_domain_excluded():
    pages = {
        'https://example.nl/contact': '<html><body>jan.jansen@example.nl info@example.nl</body></html>'
    }
    found = find_contacts(pages, 'https://example.nl')
    assert {x['email'] for x in found} == {'info@example.nl'}


def test_non_general_localpart_excluded():
    pages = {
        'https://example.nl/contact': '<html><body>klachten@example.nl (geldig) en marketing@example.nl</body></html>'
    }
    found = find_contacts(pages, 'https://example.nl')
    assert found == []


def test_free_mail_and_foreign_domain_excluded():
    pages = {
        'https://example.nl/contact': '<html><body>info@gmail.com en info@anderbedrijf.nl</body></html>'
    }
    found = find_contacts(pages, 'https://example.nl')
    assert found == []


def test_obfuscated_address_on_own_domain():
    pages = {
        'https://example.nl/contact': '<html><body>Mail: info[at]example[dot]nl for business.</body></html>'
    }
    found = find_contacts(pages, 'https://example.nl')
    assert {x['email'] for x in found} == {'info@example.nl'}
