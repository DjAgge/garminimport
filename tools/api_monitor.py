# -*- coding: utf-8 -*-

"""
File: tools/api_monitor.py

Description:    Monitors the Garmin Connect API for changes, so that API
                changes are noticed early instead of only surfacing as a
                failing export run.

                The monitor probes the endpoints that gcexport.py depends on:

                - public (no login): the SSO sign-in page, the activity/event
                  type property files on connect.garmin.com and the
                  unauthenticated behaviour of connectapi.garmin.com
                - authenticated (session restored from --tokenfile, exactly
                  like the daily export): socialProfile, the activity list,
                  userstats, the v2 daily summaries and the daily health
                  endpoints

                For JSON endpoints the response structure is checked against
                the sample responses in json/ plus the minimal set of keys the
                export code reads; a non-JSON (e.g. HTML) answer, a wrong
                status code or missing keys all count as an API change.

                On a detected change the monitor prints a report, exits with
                code 1 and notifies:

                - a deduplicated GitHub issue in the repository when
                  GITHUB_TOKEN is set (in GitHub Actions the automatic
                  GITHUB_TOKEN with "issues: write" works; on the NAS use a
                  fine-grained PAT with issues:write)
                - a webhook when MONITOR_WEBHOOK_URL is set (receives the JSON
                  report)

Configuration via environment variables:

    GITHUB_TOKEN          token for opening/commenting the GitHub issue
    GITHUB_REPOSITORY     owner/repo for the issue (default DjAgge/garminimport)
    MONITOR_WEBHOOK_URL   optional webhook that receives the JSON report
    GARMIN_USERNAME       fallback credentials for a full login when the
    GARMIN_PASSWORD       token file cannot be restored (MFA accounts should
                          use the token file instead; a fresh login prompts
                          for the MFA code)

Exit codes: 0 = all probes OK, 1 = API change detected, 2 = authenticated
probes requested but no session and no credentials available, 3 = the monitor
itself could not run (e.g. curl_cffi missing).
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import garmin_tls_patch  # noqa: E402

if garmin_tls_patch.curl_requests is None:
    sys.exit('curl_cffi is required (pip install curl_cffi)')

JSON_DIR = os.path.join(REPO_ROOT, 'json')
CONNECT_API = garmin_tls_patch.CONNECT_API

ISSUE_TITLE_PREFIX = 'Garmin API change detected'
GITHUB_API = 'https://api.github.com'

PUBLIC_PROBES = [
    {
        'name': 'sso-signin-page',
        'url': f'{garmin_tls_patch.SSO}/mobile/sso/en/sign-in?clientId={garmin_tls_patch.CLIENT_ID}',
        'kind': 'page',
    },
    {
        'name': 'activity-types-properties',
        'url': 'https://connect.garmin.com/modern/main/js/properties/activity_types/activity_types.properties',
        'kind': 'properties',
        'sample': 'activity_types.properties',
    },
    {
        'name': 'event-types-properties',
        'url': 'https://connect.garmin.com/modern/main/js/properties/event_types/event_types.properties',
        'kind': 'properties',
        'sample': 'event_types.properties',
    },
    {
        'name': 'connectapi-unauthenticated',
        'url': f'{CONNECT_API}/userprofile-service/socialProfile',
        'kind': 'not-html',
    },
]


def auth_probes(today):
    """The authenticated probes; *today* is the ISO date used in the range URLs."""
    return [
        {
            'name': 'socialProfile',
            'url': f'{CONNECT_API}/userprofile-service/socialProfile',
            'kind': 'dict',
            'required': ['id'],
            'required_any': [['displayName', 'userName']],
            'store': 'profile',
        },
        {
            'name': 'activity-list',
            'url': f'{CONNECT_API}/activitylist-service/activities/search/activities?start=0&limit=1',
            'kind': 'list',
            'sample': 'activities-list.json',
        },
        {
            'name': 'userstats',
            'url': lambda ctx: (
                f"{CONNECT_API}/userstats-service/statistics/{ctx['display_name']}" if ctx.get('display_name') else None
            ),
            'kind': 'dict',
            'sample': 'userstats.json',
        },
        {
            'name': 'dailysummaries-v2',
            'url': f'{CONNECT_API}/usersummary-service/v2/dailysummaries?startDate={today}&endDate={today}',
            'kind': 'dict',
            'required': ['data'],
            'data_element_required': ['calendarDate'],
        },
        {
            'name': 'sleep',
            'url': f'{CONNECT_API}/sleep-service/sleep/dailySleepData?date={today}',
            'kind': 'dict',
        },
        {
            'name': 'body-battery',
            'url': f'{CONNECT_API}/wellness-service/wellness/bodyBattery/events/{today}',
            'kind': 'list',
        },
        {
            'name': 'hrv-range',
            'url': f'{CONNECT_API}/hrv-service/hrv/daily/{today}/{today}',
            'kind': 'dict',
        },
        {
            'name': 'weight-range',
            'url': f'{CONNECT_API}/weight-service/weight/range/{today}/{today}?includeAll=true',
            'kind': 'list',
        },
    ]


def make_public_fetcher():
    """Fetcher for the unauthenticated probes; returns (status, body) and does not raise on HTTP errors."""

    def fetch(url):
        try:
            sess = garmin_tls_patch._new_session()
            resp = sess.get(url, headers=garmin_tls_patch.SSO_PAGE_HEADERS)
            return resp.status_code, resp.content
        except garmin_tls_patch.curl_requests.exceptions.RequestException as ex:
            return None, str(ex).encode()

    return fetch


def make_auth_fetcher():
    """Fetcher through the authenticated session; returns (status, body) also for error statuses."""

    def fetch(url):
        try:
            return 200, garmin_tls_patch.CLIENT.http_request(url)
        except garmin_tls_patch.GarminTlsException as ex:
            resp = getattr(ex, 'response', None)
            if resp is not None:
                return resp.status_code, resp.content
            return None, str(ex).encode()

    return fetch


def today_str():
    return date.today().isoformat()


def parse_properties(text):
    """Keys of a Garmin .properties file (lines 'key=value', '#' comments)."""
    keys = set()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            keys.add(line.split('=', 1)[0].strip())
    return keys


def sample_keys(sample):
    """Top-level keys of a checked-in sample response (for arrays: of the first element)."""
    with open(os.path.join(JSON_DIR, sample), encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, list):
        data = data[0] if data else {}
    return set(data.keys())


def sample_property_keys(sample):
    """Keys of a checked-in sample .properties file."""
    with open(os.path.join(JSON_DIR, sample), encoding='utf-8') as f:
        return parse_properties(f.read())


def expected_keys(probe):
    keys = set(probe.get('required', []))
    if probe.get('sample'):
        keys |= sample_keys(probe['sample'])
    return keys


def snippet(body, limit=200):
    text = body.decode('utf-8', errors='replace') if isinstance(body, bytes) else str(body)
    text = ' '.join(text.split())
    return text[:limit] + ('...' if len(text) > limit else '')


def check_structure(probe, data):
    """Return a list of structure problems for a parsed JSON response of the probe's kind."""
    problems = []
    kind = probe['kind']
    if kind == 'dict':
        if not isinstance(data, dict):
            return [f'response is a JSON {type(data).__name__}, expected an object']
        missing = expected_keys(probe) - set(data.keys())
        if missing:
            problems.append('missing keys: ' + ', '.join(sorted(missing)))
        for group in probe.get('required_any', []):
            if not any(key in data for key in group):
                problems.append('none of these keys present: ' + ', '.join(group))
        elements = data.get('data') if isinstance(data.get('data'), list) else []
        element_required = probe.get('data_element_required', [])
    elif kind == 'list':
        if not isinstance(data, list):
            return [f'response is a JSON {type(data).__name__}, expected an array']
        if not data:
            return []
        if not isinstance(data[0], dict):
            return ['the first array element is not a JSON object']
        missing = expected_keys(probe) - set(data[0].keys())
        if missing:
            problems.append('missing keys in the first array element: ' + ', '.join(sorted(missing)))
        elements = data
        element_required = probe.get('element_required', [])
    else:
        return []
    if element_required and elements and isinstance(elements[0], dict):
        missing = set(element_required) - set(elements[0].keys())
        if missing:
            problems.append('missing keys in the data element: ' + ', '.join(sorted(missing)))
    return problems


def run_probe(probe, fetch, ctx, today):
    """Run one probe and return a result dict {name, url, ok, detail}."""
    url = probe['url']
    if callable(url):
        url = url(ctx)
    if url is None:
        return {'name': probe['name'], 'url': probe['name'], 'ok': True, 'detail': 'skipped: no display name from socialProfile'}
    if '{today}' in url:
        url = url.format(today=today)
    status, body = fetch(url)
    result = {'name': probe['name'], 'url': url, 'ok': True, 'detail': ''}
    kind = probe['kind']
    if status is None:
        result.update(ok=False, detail=f'request failed: {snippet(body)}')
        return result
    if kind == 'page':
        low = body.lower()
        if not 200 <= status < 300:
            result.update(ok=False, detail=f'HTTP {status}: {snippet(body)}')
        elif b'<html' not in low or b'garmin' not in low:
            result.update(ok=False, detail=f'HTTP {status}, but not the expected Garmin HTML page: {snippet(body)}')
    elif kind == 'not-html':
        if status == 200:
            result.update(ok=False, detail='expected HTTP 401/403 without a login, got 200')
        elif status not in (401, 403):
            result.update(ok=False, detail=f'unexpected HTTP {status}: {snippet(body)}')
        elif b'<html' in body.lower():
            result.update(ok=False, detail=f'HTTP {status}, but the answer is HTML (endpoint moved?): {snippet(body)}')
    elif kind == 'properties':
        if status != 200:
            result.update(ok=False, detail=f'HTTP {status}: {snippet(body)}')
        elif b'<html' in body.lower():
            result.update(
                ok=False,
                detail=f'HTTP 200, but the answer is HTML instead of a .properties file (endpoint moved?): {snippet(body)}',
            )
        else:
            missing = sample_property_keys(probe['sample']) - parse_properties(body.decode('utf-8', errors='replace'))
            if missing:
                result.update(ok=False, detail='missing keys: ' + ', '.join(sorted(missing)))
    else:
        if status != 200:
            result.update(ok=False, detail=f'HTTP {status}: {snippet(body)}')
        elif not body:
            result['detail'] = 'empty response (HTTP 204)'
        else:
            try:
                data = json.loads(body)
            except ValueError:
                result.update(ok=False, detail=f'response is not JSON (endpoint moved or auth changed?): {snippet(body)}')
            else:
                problems = check_structure(probe, data)
                if problems:
                    result.update(ok=False, detail='; '.join(problems))
                elif probe.get('store') == 'profile' and isinstance(data, dict):
                    ctx['display_name'] = data.get('displayName') or data.get('userName')
    return result


def run_monitor(probe_set, public_fetch=None, auth_fetch=None, login_error=None, today=None):
    """Run the probes of the given set and return (exit_code, report)."""
    today = today or today_str()
    ctx = {}
    results = []
    if login_error:
        results.append({'name': 'login', 'url': garmin_tls_patch.SSO, 'ok': False, 'detail': login_error})
    if probe_set in ('public', 'all') and public_fetch:
        for probe in PUBLIC_PROBES:
            results.append(run_probe(probe, public_fetch, ctx, today))
    auth_unavailable = False
    if probe_set in ('auth', 'all') and not login_error:
        if auth_fetch is None:
            auth_unavailable = True
        else:
            for probe in auth_probes(today):
                results.append(run_probe(probe, auth_fetch, ctx, today))
    problems = [r for r in results if not r['ok']]
    report = {
        'date': today,
        'ok': not problems,
        'auth_unavailable': auth_unavailable,
        'results': results,
        'problems': problems,
    }
    if problems:
        code = 1
    elif auth_unavailable:
        code = 2
    else:
        code = 0
    return code, report


def render_report_text(report):
    lines = [f"Garmin API monitor report {report['date']}: {'OK' if report['ok'] else 'PROBLEMS DETECTED'}"]
    for r in report['results']:
        mark = 'OK  ' if r['ok'] else 'FAIL'
        line = f'  [{mark}] {r["name"]} ({r["url"]})'
        if r['detail']:
            line += f' - {r["detail"]}'
        lines.append(line)
    if report['auth_unavailable']:
        lines.append(
            '  authenticated probes skipped: no session and no credentials (set --tokenfile or GARMIN_USERNAME/GARMIN_PASSWORD)'
        )
    return '\n'.join(lines)


def render_issue_body(report):
    lines = [
        f'**Date:** {report["date"]}',
        '',
        'The Garmin API monitor detected a possible API change:',
        '',
    ]
    for p in report['problems']:
        lines.append(f"- **{p['name']}** (`{p['url']}`): {p['detail']}")
    run_id = os.environ.get('GITHUB_RUN_ID')
    if run_id:
        repo = os.environ.get('GITHUB_REPOSITORY', 'DjAgge/garminimport')
        server = os.environ.get('GITHUB_SERVER_URL', 'https://github.com')
        lines += ['', f'Workflow run: {server}/{repo}/actions/runs/{run_id}']
    lines += [
        '',
        '---',
        'Created automatically by `tools/api_monitor.py`. Close this issue once the change is handled; a new detection opens a new issue.',
    ]
    return '\n'.join(lines)


def github_notify(repo, token, report, dry_run):
    """Open a deduplicated issue (or comment on the existing open one) in *repo*."""
    title = f'{ISSUE_TITLE_PREFIX}: {report["problems"][0]["name"]} ({report["date"]})'
    body = render_issue_body(report)
    if dry_run:
        print(f'DRY RUN: would open/comment a GitHub issue in {repo}: {title}')
        return
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    }

    def call(method, url, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b'null')

    query = urllib.parse.urlencode({'q': f'repo:{repo} is:open in:title:{ISSUE_TITLE_PREFIX}'})
    try:
        found = call('GET', f'{GITHUB_API}/search/issues?{query}')
        open_issues = found.get('items', [])
        if open_issues:
            number = open_issues[0]['number']
            call('POST', f'{GITHUB_API}/repos/{repo}/issues/{number}/comments', {'body': body})
            print(f'Commented on the existing issue #{number} in {repo}: {title}')
        else:
            issue = call('POST', f'{GITHUB_API}/repos/{repo}/issues', {'title': title, 'body': body})
            print(f'Opened issue #{issue["number"]} in {repo}: {title}')
    except (urllib.error.URLError, ValueError) as ex:
        print(f'WARNING: could not notify via a GitHub issue in {repo}: {ex}')


def webhook_notify(url, report, dry_run):
    if dry_run:
        print(f'DRY RUN: would POST the report to {url}')
        return
    payload = json.dumps(report, indent=2).encode()
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f'Posted the monitor report to the webhook (HTTP {resp.status})')
    except urllib.error.URLError as ex:
        print(f'WARNING: could not reach the webhook: {ex}')


def ensure_session(args):
    """Restore the session from the token file or log in with credentials; returns (fetcher, login_error)."""
    token_file = os.path.expanduser(args.tokenfile) if args.tokenfile else None
    if token_file and garmin_tls_patch.CLIENT.restore(token_file):
        return make_auth_fetcher(), None
    username = args.username or os.environ.get('GARMIN_USERNAME')
    password = args.password or os.environ.get('GARMIN_PASSWORD')
    if username and password:
        try:
            garmin_tls_patch.CLIENT.login(username, password, token_file=token_file)
            return make_auth_fetcher(), None
        except garmin_tls_patch.GarminTlsException as ex:
            return None, str(ex)
    return None, None


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='Probe the Garmin Connect endpoints used by gcexport.py and report API changes (see the module docstring).',
    )
    parser.add_argument(
        '--tokenfile',
        default='~/.gcexport_tokens.json',
        help='JSON file with the cached OAuth tokens (same as gcexport.py --tokenfile), used to restore the session without a login',
    )
    parser.add_argument('--username', help='Garmin username for a full login (fallback: GARMIN_USERNAME)')
    parser.add_argument('--password', help='Garmin password for a full login (fallback: GARMIN_PASSWORD)')
    parser.add_argument('--probe-set', choices=['public', 'auth', 'all'], default='all', help='which probes to run (default: all)')
    parser.add_argument(
        '--repo',
        default=os.environ.get('GITHUB_REPOSITORY', 'DjAgge/garminimport'),
        help='repository for the GitHub issue notification (default: GITHUB_REPOSITORY or DjAgge/garminimport)',
    )
    parser.add_argument(
        '--github-token',
        default=os.environ.get('GITHUB_TOKEN'),
        help='token for the GitHub issue notification (default: GITHUB_TOKEN)',
    )
    parser.add_argument('--dry-run', action='store_true', help='do not open issues or call webhooks, only print')
    parser.add_argument('--report', help='write the JSON report to this file')
    parser.add_argument('-v', '--verbose', action='count', default=0)
    return parser.parse_args(argv[1:])


def main(argv):
    args = parse_args(argv)
    login_error = None
    auth_fetch = None
    if args.probe_set in ('auth', 'all'):
        auth_fetch, login_error = ensure_session(args)
    public_fetch = make_public_fetcher() if args.probe_set in ('public', 'all') else None
    code, report = run_monitor(args.probe_set, public_fetch=public_fetch, auth_fetch=auth_fetch, login_error=login_error)
    print(render_report_text(report))
    if args.report:
        with open(args.report, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2)
    if report['problems']:
        print()
        print('GARMIN API CHANGE DETECTED - see the failing probes above')
        if args.github_token:
            github_notify(args.repo, args.github_token, report, args.dry_run)
        else:
            print('No GITHUB_TOKEN set; skipping the GitHub issue notification')
        webhook = os.environ.get('MONITOR_WEBHOOK_URL')
        if webhook:
            webhook_notify(webhook, report, args.dry_run)
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv))
