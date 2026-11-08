#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
File: garmin_tls_patch.py

Description:    Modern Garmin Connect login for gcexport.py.

                Garmin's SSO endpoints are protected by Cloudflare and reject
                requests whose TLS fingerprint does not look like a real
                browser. This module therefore uses curl_cffi (which imitates
                a browser's TLS handshake) for all HTTP traffic and implements
                the current login flow:

                  1. SSO sign-in page (to obtain session cookies)
                  2. POST /sso/mobile/api/login with username/password
                  3. optional MFA verification
                  4. OAuth1 GET /oauth-service/oauth/preauthorized (with the
                     SSO ticket) to obtain an OAuth1 request token
                  5. OAuth1 POST /oauth-service/oauth/exchange/user/2.0 to
                     obtain the OAuth2 access token used for all further calls

                The OAuth1 signature is built by hand and follows oauthlib's
                semantics exactly: an empty oauth_token is omitted from the
                Authorization header and from the signature base string
                (sending oauth_token="" makes Garmin's server reject the
                signature with HTTP 401).
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit

try:
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover
    curl_requests = None

SSO = "https://sso.garmin.com"
CONNECT_API = "https://connectapi.garmin.com"
CLIENT_ID = "GCM_ANDROID_DARK"
SERVICE_URL = "https://mobile.integration.garmin.com/gcm/android"
OAUTH_CONSUMER_KEY = "fc3e99d2-118c-44b8-8ae3-03370dde24c0"
OAUTH_CONSUMER_SECRET = 'E08WAR897WEy2knn7aFBrvegVAf0AFdWBBF'
OAUTH_USER_AGENT = 'com.garmin.android.apps.connectmobile'
SSO_SUCCESSFUL = 'SUCCESSFUL'
SSO_MFA_REQUIRED = 'MFA_REQUIRED'

# Browser-like headers for the SSO endpoints; the SSO pages run in a WebView,
# so requests must look like a browser to avoid Cloudflare challenges.
SSO_PAGE_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) ' 'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Dest': 'document',
}

# Default headers for the Connect API endpoints (same as the old urllib path).
DEFAULT_HEADERS = {
    'nk': 'NT',  # necessary since 2021-02-23 to avoid http error code 402
    'User-Agent': ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ' '(KHTML, like Gecko) Chrome/54.0.2816.0 Safari/537.36'),
}


class GarminTlsException(Exception):
    """Raised for login or HTTP errors, including Garmin's response body."""

    def __init__(self, message, response=None):
        if response is not None:
            body = response.text
            if len(body) > 500:
                body = body[:500] + '...'
            message = f'{message}\nHTTP status: {response.status_code}\nResponse body: {body}'
        super().__init__(message)


class GarminClient:
    """Holds the authenticated curl_cffi session and performs the requests."""

    def __init__(self):
        self.session = None
        self.oauth1_token = None
        self.oauth2_token = None

    def login(self, username, password, token_file=None):
        """
        Log in to Garmin Connect with the modern SSO/OAuth flow.

        Prompts for an MFA code if the account requires it. On success the
        module-level *CLIENT* holds an authenticated curl_cffi session whose
        requests carry the OAuth2 bearer token. If *token_file* is given,
        the OAuth tokens are saved there so that later runs can skip the
        login (and thus the MFA prompt) via restore().
        """
        sess = _new_session()

        # Get a valid session cookie by pulling the sign-in page first.
        sess.get(
            f'{SSO}/mobile/sso/en/sign-in',
            params={'clientId': CLIENT_ID},
            headers={**SSO_PAGE_HEADERS, 'Sec-Fetch-Site': 'none'},
        )

        login_params = {'clientId': CLIENT_ID, 'locale': 'en-US', 'service': SERVICE_URL}
        resp = sess.post(
            f'{SSO}/mobile/api/login',
            params=login_params,
            headers=SSO_PAGE_HEADERS,
            json={
                'username': username,
                'password': password,
                'rememberMe': False,
                'captchaToken': '',
            },
        )
        try:
            resp_json = resp.json()
        except ValueError:
            resp_json = {}
        resp_type = resp_json.get('responseStatus', {}).get('type')

        if resp_type == SSO_SUCCESSFUL:
            ticket = resp_json['serviceTicketId']
        elif resp_type == SSO_MFA_REQUIRED:
            mfa_info = resp_json.get('customerMfaInfo') or {}
            mfa_method = mfa_info.get('mfaLastMethodUsed') or 'email'
            ticket = _handle_mfa(sess, login_params, mfa_method)
        else:
            message = f'Garmin SSO login failed: {_sso_error(resp_json)}'
            if resp.status_code == 429:
                message += (
                    "\nGarmin is rate limiting the login (HTTP 429)."
                    " Too many login attempts in a short time;"
                    " wait 15-30 minutes before trying again."
                )
            raise GarminTlsException(message, resp)

        # Sets the Cloudflare load balancer cookie for backend pinning (best effort).
        try:
            sess.get(
                f'{SSO}/portal/sso/embed',
                headers={**SSO_PAGE_HEADERS, 'Sec-Fetch-Site': 'same-origin'},
            )
        except curl_requests.exceptions.RequestException as ex:
            logging.debug('Ignoring error from sso/embed: %s', ex)

        oauth1_token = get_oauth1_token(ticket, sess)
        oauth2_token = set_expirations(exchange(oauth1_token, sess))
        logging.debug('OAuth2 token expires in %s s', oauth2_token.get('expires_in'))

        sess.headers.update(
            {
                'Authorization': f"Bearer {oauth2_token['access_token']}",
                'User-Agent': OAUTH_USER_AGENT,
            }
        )
        self.session = sess
        self.oauth1_token = oauth1_token
        self.oauth2_token = oauth2_token
        if token_file:
            save_tokens(token_file, oauth1_token, oauth2_token)
        return oauth2_token

    def restore(self, token_file):
        """
        Restore a session from the tokens in *token_file* without an SSO
        login (and thus without an MFA prompt). A still-valid OAuth2 token is
        used directly; an expired one is renewed from the OAuth1 token.

        :param token_file:  path of the JSON file with the saved tokens
        :return:            True if the session could be restored
        """
        if not token_file:
            return False
        tokens = load_tokens(token_file)
        if not tokens:
            return False
        oauth1_token, oauth2_token = tokens
        try:
            sess = _new_session()
            if time.time() >= oauth2_token.get('expires_at', 0) - 60:
                logging.info('OAuth2 token expired, renewing from the OAuth1 token')
                oauth2_token = set_expirations(exchange(oauth1_token, sess))
                save_tokens(token_file, oauth1_token, oauth2_token)
            sess.headers.update(
                {
                    'Authorization': f"Bearer {oauth2_token['access_token']}",
                    'User-Agent': OAUTH_USER_AGENT,
                }
            )
        except (GarminTlsException, curl_requests.exceptions.RequestException, OSError, ValueError) as ex:
            logging.warning('Could not restore the session from %s: %s', token_file, ex)
            return False
        self.session = sess
        self.oauth1_token = oauth1_token
        self.oauth2_token = oauth2_token
        logging.info('Session restored from %s', token_file)
        return True

    def session_active(self):
        """True after a successful login()."""
        return self.session is not None

    def http_request(self, url, post=None, headers=None):
        """
        Perform an HTTP request through the authenticated curl_cffi session.

        Drop-in replacement for gcexport.http_req: same signature, returns the
        response body as bytes. Error messages include Garmin's response body,
        which makes 401/403 problems diagnosable.
        """
        if not self.session_active():
            raise GarminTlsException('Not logged in; call login() first')
        all_headers = dict(DEFAULT_HEADERS)
        if headers:
            all_headers.update(headers)
        if post:
            resp = self.session.post(url, data=urlencode(post), headers=all_headers)
        else:
            resp = self.session.get(url, headers=all_headers)
        if resp.status_code == 204:
            logging.info('Got 204 for %s, returning empty response', url)
            return b''
        if resp.status_code != 200:
            raise GarminTlsException(f'Bad return code ({resp.status_code}) for: {url}', resp)
        return resp.content


def percent_encode(value):
    """Percent-encode *value* according to RFC 3986 (as OAuth1 requires)."""
    return quote(str(value), safe='~')


def signature_base_string(method, url, params):
    """
    Build the OAuth1 signature base string.

    *params* is the full list of parameters that go into the signature:
    query parameters, OAuth parameters and (for form posts) the body
    parameters. Returns the base string per RFC 5849 section 3.4.1.
    """
    method = method.upper()
    parts = urlsplit(url)
    base_url = f'{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path}'
    encoded = '&'.join(
        f'{percent_encode(key)}={percent_encode(value)}' for key, value in sorted(params, key=lambda kv: (kv[0], kv[1]))
    )
    return '&'.join((method, percent_encode(base_url), percent_encode(encoded)))


def oauth1_sign(
    method,
    url,
    *,
    oauth_token='',
    oauth_token_secret='',
    extra_params=None,
    nonce=None,
    timestamp=None,
    consumer_key=OAUTH_CONSUMER_KEY,
    consumer_secret=OAUTH_CONSUMER_SECRET,
):
    """
    Compute the OAuth1 HMAC-SHA1 signature for a request.

    Mirrors oauthlib: an empty *oauth_token* is omitted completely from the
    signed parameters (Garmin rejects oauth_token="" with HTTP 401).
    *extra_params* are additional query/body parameters to include.
    Returns the full Authorization header value.
    """
    if nonce is None:
        nonce = secrets.token_hex(16)
    if timestamp is None:
        timestamp = str(int(time.time()))

    oauth_params = {
        'oauth_consumer_key': consumer_key,
        'oauth_nonce': nonce,
        'oauth_signature_method': 'HMAC-SHA1',
        'oauth_timestamp': timestamp,
        'oauth_version': '1.0',
    }
    if oauth_token:
        oauth_params['oauth_token'] = oauth_token

    # Like oauthlib, the URL query parameters are part of the signature.
    query = parse_qs(urlsplit(url).query)
    all_extra = {k: v[0] for k, v in query.items()}
    all_extra.update(extra_params or {})
    params = list(oauth_params.items()) + list(all_extra.items())
    base = signature_base_string(method, url, params)
    key = '&'.join((percent_encode(consumer_secret), percent_encode(oauth_token_secret)))
    digest = hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    signature = base64.b64encode(digest).decode()

    oauth_params['oauth_signature'] = signature
    header = ', '.join(f'{k}="{percent_encode(v)}"' for k, v in oauth_params.items())
    logging.debug('OAuth1 signature base string:\n%s', base)
    return f'OAuth {header}'


def _require_curl_cffi():
    if curl_requests is None:
        raise GarminTlsException('curl_cffi is required for the Garmin login (pip install curl_cffi)')


def _new_session():
    _require_curl_cffi()
    return curl_requests.Session(impersonate='chrome')


def _sso_error(resp_json):
    status = resp_json.get('responseStatus', {})
    return f"{status.get('type', 'UNKNOWN')}: {status.get('message', '')}"


def _handle_mfa(sess, login_params, mfa_method):
    code = input(f'MFA code ({mfa_method}): ').strip()
    resp = sess.post(
        f'{SSO}/mobile/api/mfa/verifyCode',
        params=login_params,
        headers=SSO_PAGE_HEADERS,
        json={
            'mfaMethod': mfa_method,
            'mfaVerificationCode': code,
            'rememberMyBrowser': False,
            'reconsentList': [],
            'mfaSetup': False,
        },
    )
    try:
        resp_json = resp.json()
    except ValueError:
        resp_json = {}
    if resp_json.get('responseStatus', {}).get('type') != SSO_SUCCESSFUL:
        raise GarminTlsException(f'MFA verification failed: {_sso_error(resp_json)}', resp)
    return resp_json['serviceTicketId']


def set_expirations(token):
    """Add absolute expiry timestamps (epoch seconds) to the OAuth2 token."""
    token['expires_at'] = int(time.time() + token.get('expires_in', 0))
    token['refresh_token_expires_at'] = int(time.time() + token.get('refresh_token_expires_in', 0))
    return token


def save_tokens(token_file, oauth1_token, oauth2_token):
    """
    Save the OAuth tokens as JSON to *token_file* (readable only by the
    owner). Failures are logged and otherwise ignored (the export continues
    with the tokens in memory).
    """
    try:
        with open(os.path.expanduser(token_file), 'w', encoding='utf-8') as token_json:
            json.dump({'oauth1': oauth1_token, 'oauth2': oauth2_token}, token_json, indent=2)
        os.chmod(os.path.expanduser(token_file), 0o600)
    except OSError as ex:
        logging.warning('Could not save the tokens to %s: %s', token_file, ex)


def load_tokens(token_file):
    """
    Load the OAuth tokens from *token_file*.

    :param token_file:  path of the JSON file with the saved tokens
    :return:            (oauth1_token, oauth2_token) tuple, or None if absent
    """
    try:
        with open(os.path.expanduser(token_file), encoding='utf-8') as token_json:
            tokens = json.load(token_json)
        return tokens['oauth1'], tokens['oauth2']
    except (OSError, ValueError, KeyError) as ex:
        logging.debug('No usable tokens in %s: %s', token_file, ex)
        return None


def get_oauth1_token(ticket, sess):
    """Exchange the SSO ticket for an OAuth1 token (the 'preauthorized' call)."""
    url = f'{CONNECT_API}/oauth-service/oauth/preauthorized' f'?ticket={ticket}&login-url={SERVICE_URL}&accepts-mfa-tokens=true'
    auth = oauth1_sign('GET', url)
    resp = sess.get(url, headers={'Authorization': auth, 'User-Agent': OAUTH_USER_AGENT})
    if resp.status_code != 200:
        raise GarminTlsException('OAuth1 token request (preauthorized) failed', resp)
    token = {k: v[0] for k, v in parse_qs(resp.text).items()}
    if 'oauth_token' not in token or 'oauth_token_secret' not in token:
        raise GarminTlsException(f'OAuth1 token request (preauthorized) returned no token: {resp.text}')
    return token


def exchange(oauth1_token, sess):
    """Exchange the OAuth1 token for the OAuth2 access token."""
    url = f'{CONNECT_API}/oauth-service/oauth/exchange/user/2.0'
    body = {'audience': 'GARMIN_CONNECT_MOBILE_ANDROID_DI'}
    if oauth1_token.get('mfa_token'):
        body['mfa_token'] = oauth1_token['mfa_token']
    auth = oauth1_sign(
        'POST',
        url,
        oauth_token=oauth1_token['oauth_token'],
        oauth_token_secret=oauth1_token['oauth_token_secret'],
        extra_params=body,
    )
    resp = sess.post(
        url,
        headers={
            'Authorization': auth,
            'User-Agent': OAUTH_USER_AGENT,
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        data=urlencode(body),
    )
    if resp.status_code != 200:
        raise GarminTlsException('OAuth2 token exchange failed', resp)
    return resp.json()


# The single client used by gcexport.py; its session is set by login().
CLIENT = GarminClient()
