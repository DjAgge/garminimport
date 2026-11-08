#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
File: garmin_tls_patch_test.py

Description:    Unit tests for the OAuth1 signing in garmin_tls_patch.py.

                The hand-built signature is compared against oauthlib (the
                reference implementation used by requests_oauthlib) for both
                the 'preauthorized' call (no oauth_token, which must be
                omitted completely) and the 'exchange' call (with an
                oauth_token and form body parameters). A hardcoded
                X/Twitter reference vector guards against regressions even
                when oauthlib is not installed.
"""

import garmin_tls_patch as tls

import pytest

try:
    from requests_oauthlib import OAuth1
    import requests

    HAVE_OAUTHLIB = True
except ImportError:
    HAVE_OAUTHLIB = False

NONCE = 'kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg'
TIMESTAMP = '1318622958'


def oauthlib_signature(method, url, body=None, token=None, token_secret=None):
    """Sign *url* with requests_oauthlib and return the oauth_signature value."""
    auth = OAuth1(
        tls.OAUTH_CONSUMER_KEY,
        client_secret=tls.OAUTH_CONSUMER_SECRET,
        resource_owner_key=token,
        resource_owner_secret=token_secret,
        nonce=NONCE,
        timestamp=TIMESTAMP,
    )
    req = requests.Request(method, url, data=body, auth=auth).prepare()
    header = req.headers['Authorization']
    if isinstance(header, bytes):
        header = header.decode()
    for part in header.split(','):
        key, _, value = part.strip().partition('=')
        if key == 'oauth_signature':
            return value.strip('"')
    raise AssertionError('oauth_signature not found in header')


def parse_signature(auth_header):
    for part in auth_header[len('OAuth ') :].split(', '):
        key, _, value = part.partition('=')
        if key == 'oauth_signature':
            return value.strip('"')
    raise AssertionError('oauth_signature not found')


def test_empty_token_omitted_from_header():
    auth = tls.oauth1_sign(
        'GET', 'https://connectapi.garmin.com/oauth-service/oauth/preauthorized', nonce=NONCE, timestamp=TIMESTAMP
    )
    assert 'oauth_token' not in auth


def test_nonempty_token_in_header():
    auth = tls.oauth1_sign(
        'GET',
        'https://connectapi.garmin.com/oauth-service/oauth/exchange/user/2.0',
        oauth_token='the-token',
        oauth_token_secret='the-secret',
        nonce=NONCE,
        timestamp=TIMESTAMP,
    )
    assert 'oauth_token="the-token"' in auth


@pytest.mark.skipif(not HAVE_OAUTHLIB, reason='requests_oauthlib not installed')
def test_signature_matches_oauthlib_preauthorized():
    # The 401 case: no oauth_token, query parameters must be signed.
    url = (
        'https://connectapi.garmin.com/oauth-service/oauth/preauthorized'
        '?ticket=ST-1234&login-url=https%3A%2F%2Fmobile.integration.garmin.com%2Fgcm%2Fandroid'
        '&accepts-mfa-tokens=true'
    )
    expected = oauthlib_signature('GET', url)
    actual = parse_signature(tls.oauth1_sign('GET', url, nonce=NONCE, timestamp=TIMESTAMP))
    assert expected == actual


@pytest.mark.skipif(not HAVE_OAUTHLIB, reason='requests_oauthlib not installed')
def test_signature_matches_oauthlib_exchange():
    # Form body parameters must be part of the signature.
    url = 'https://connectapi.garmin.com/oauth-service/oauth/exchange/user/2.0'
    body = {'audience': 'GARMIN_CONNECT_MOBILE_ANDROID_DI'}
    expected = oauthlib_signature('POST', url, body=body, token='the-token', token_secret='the-secret')
    actual = parse_signature(
        tls.oauth1_sign(
            'POST',
            url,
            oauth_token='the-token',
            oauth_token_secret='the-secret',
            extra_params=body,
            nonce=NONCE,
            timestamp=TIMESTAMP,
        )
    )
    assert expected == actual


def test_twitter_reference_vector():
    # Known-answer test from the X/Twitter OAuth1 documentation
    # ("Creating a signature"), using Twitter's documented keys.
    auth = tls.oauth1_sign(
        'POST',
        'https://api.twitter.com/1.1/statuses/update.json?include_entities=true',
        oauth_token='370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9WeJYbP',
        oauth_token_secret='LswwdoUaIvS8ltyTt5jkRhjyVZ8Ny8tMdfBxDJ4W',
        extra_params={
            'include_entities': 'true',
            'status': 'Hello Ladies + Gentlemen, a signed OAuth request!',
        },
        nonce=NONCE,
        timestamp=TIMESTAMP,
        consumer_key='xvz1evfs4wQoTEpFhkI8xw',
        consumer_secret='kAcSOqF21Fu85e7zjL7CwGAVzU',
    )
    assert 'wvMJArPeDssy2e%2FxaxyCwHkHphY%3D' in auth


def test_token_save_load_roundtrip(tmp_path):
    token_file = str(tmp_path / 'tokens.json')
    oauth1 = {'oauth_token': 't1', 'oauth_token_secret': 's1'}
    oauth2 = {'access_token': 'a1', 'expires_in': 1000}
    tls.save_tokens(token_file, oauth1, tls.set_expirations(oauth2))
    loaded = tls.load_tokens(token_file)
    assert loaded is not None
    assert loaded[0] == oauth1
    assert loaded[1]['access_token'] == 'a1'
    assert loaded[1]['expires_at'] > 0
    assert (tmp_path / 'tokens.json').stat().st_mode & 0o777 == 0o600


def test_load_tokens_missing_file(tmp_path):
    assert tls.load_tokens(str(tmp_path / 'nope.json')) is None


def test_restore_missing_file(tmp_path):
    client = tls.GarminClient()
    assert client.restore(str(tmp_path / 'nope.json')) is False
    assert not client.session_active()


def test_restore_valid_token(tmp_path):
    token_file = str(tmp_path / 'tokens.json')
    tls.save_tokens(
        token_file,
        {'oauth_token': 't1', 'oauth_token_secret': 's1'},
        tls.set_expirations({'access_token': 'a1', 'expires_in': 3600}),
    )
    client = tls.GarminClient()
    assert client.restore(token_file) is True
    assert client.session_active()
    assert client.session.headers['Authorization'] == 'Bearer a1'


def test_restore_expired_token_renews(tmp_path, monkeypatch):
    token_file = str(tmp_path / 'tokens.json')
    tls.save_tokens(
        token_file,
        {'oauth_token': 't1', 'oauth_token_secret': 's1'},
        {'access_token': 'old', 'expires_in': 3600, 'expires_at': 1},
    )

    def fake_exchange(oauth1_token, sess):
        assert oauth1_token['oauth_token'] == 't1'
        return {'access_token': 'new', 'expires_in': 3600}

    monkeypatch.setattr(tls, 'exchange', fake_exchange)
    client = tls.GarminClient()
    assert client.restore(token_file) is True
    assert client.session.headers['Authorization'] == 'Bearer new'
    loaded = tls.load_tokens(token_file)
    assert loaded[1]['access_token'] == 'new'
