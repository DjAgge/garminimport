# -*- coding: utf-8 -*-
"""
Tests for tools/api_monitor.py; Call them with this command line:

py.test tools/api_monitor_test.py
"""

import json

import api_monitor


def fake_fetch(mapping):
    """A fetcher that maps URL substrings to (status, body) responses."""

    def fetch(url):
        for key, value in mapping.items():
            if key in url:
                return value
        return None, b'no fake response registered'

    return fetch


def public_ok_fetch():
    props = {
        sample: '\n'.join(f'{key}=x' for key in api_monitor.sample_property_keys(sample))
        for sample in ('activity_types.properties', 'event_types.properties')
    }
    return fake_fetch(
        {
            'sign-in': (200, b'<html><body>Garmin Sign In</body></html>'),
            'activity_types': (200, props['activity_types.properties'].encode()),
            'event_types': (200, props['event_types.properties'].encode()),
            'socialProfile': (401, b'{"error": "unauthorized"}'),
        }
    )


def test_parse_properties():
    text = '# comment\nactivity_type_running=Running\n\nactivity_type_cycling = Cycling\nnovalue\n'
    assert api_monitor.parse_properties(text) == {'activity_type_running', 'activity_type_cycling'}


def test_check_structure_dict_missing_keys():
    probe = {'kind': 'dict', 'required': ['id', 'displayName']}
    assert api_monitor.check_structure(probe, {'id': 1}) == ['missing keys: displayName']
    assert api_monitor.check_structure(probe, {'id': 1, 'displayName': 'x'}) == []


def test_check_structure_required_any():
    probe = {'kind': 'dict', 'required_any': [['displayName', 'userName']]}
    assert api_monitor.check_structure(probe, {'id': 1}) == ['none of these keys present: displayName, userName']
    assert api_monitor.check_structure(probe, {'userName': 'x'}) == []


def test_check_structure_list_uses_sample_keys():
    with open(f'{api_monitor.JSON_DIR}/activities-list.json') as f:
        element = json.load(f)[0]
    probe = {'kind': 'list', 'sample': 'activities-list.json'}
    assert api_monitor.check_structure(probe, [element]) == []
    broken = {key: value for key, value in element.items() if key != 'activityId'}
    assert api_monitor.check_structure(probe, [broken]) == ['missing keys in the first array element: activityId']
    assert api_monitor.check_structure(probe, []) == []


def test_check_structure_data_element_required():
    probe = {'kind': 'dict', 'required': ['data'], 'data_element_required': ['calendarDate']}
    assert api_monitor.check_structure(probe, {'data': [{'calendarDate': '2026-11-10'}]}) == []
    problems = api_monitor.check_structure(probe, {'data': [{'steps': 1}]})
    assert problems == ['missing keys in the data element: calendarDate']


def test_run_probe_rejects_html_answer():
    probe = {
        'name': 'socialProfile',
        'url': 'https://connectapi.garmin.com/userprofile-service/socialProfile',
        'kind': 'dict',
        'required': ['id'],
    }
    result = api_monitor.run_probe(probe, fake_fetch({'socialProfile': (200, b'<html>login</html>')}), {}, '2026-11-10')
    assert not result['ok']
    assert 'not JSON' in result['detail']


def test_run_probe_http_error():
    probe = {
        'name': 'sleep',
        'url': 'https://connectapi.garmin.com/sleep-service/sleep/dailySleepData?date={today}',
        'kind': 'dict',
    }
    result = api_monitor.run_probe(probe, fake_fetch({'sleep': (404, b'not found')}), {}, '2026-11-10')
    assert not result['ok']
    assert 'HTTP 404' in result['detail']
    assert 'date=2026-11-10' in result['url']


def test_run_probe_empty_body_is_ok():
    probe = {'name': 'sleep', 'url': 'https://x/sleep', 'kind': 'dict'}
    result = api_monitor.run_probe(probe, fake_fetch({'sleep': (200, b'')}), {}, '2026-11-10')
    assert result['ok']
    assert '204' in result['detail']


def test_run_probe_page():
    probe = {'name': 'sso', 'url': 'https://sso.garmin.com/sign-in', 'kind': 'page'}
    ok = api_monitor.run_probe(probe, fake_fetch({'sign-in': (200, b'<html>garmin</html>')}), {}, '2026-11-10')
    bad = api_monitor.run_probe(probe, fake_fetch({'sign-in': (200, b'{"json": true}')}), {}, '2026-11-10')
    assert ok['ok']
    assert not bad['ok']


def test_run_probe_not_html():
    probe = {'name': 'connectapi', 'url': 'https://connectapi.garmin.com/x', 'kind': 'not-html'}
    assert api_monitor.run_probe(probe, fake_fetch({'connectapi': (401, b'{"e":1}')}), {}, '2026-11-10')['ok']
    assert not api_monitor.run_probe(probe, fake_fetch({'connectapi': (200, b'{}')}), {}, '2026-11-10')['ok']
    assert not api_monitor.run_probe(probe, fake_fetch({'connectapi': (403, b'<html>cf</html>')}), {}, '2026-11-10')['ok']


def test_run_probe_properties_rejects_html():
    probe = {
        'name': 'activity-types-properties',
        'url': 'https://x/activity_types.properties',
        'kind': 'properties',
        'sample': 'activity_types.properties',
    }
    html = api_monitor.run_probe(probe, fake_fetch({'activity_types': (200, b'<html>sign in</html>')}), {}, '2026-11-10')
    assert not html['ok']
    assert 'HTML' in html['detail']
    keys = '\n'.join(f'{key}=x' for key in api_monitor.sample_property_keys('activity_types.properties'))
    ok = api_monitor.run_probe(probe, fake_fetch({'activity_types': (200, keys.encode())}), {}, '2026-11-10')
    assert ok['ok']


def test_run_probe_userstats_skipped_without_display_name():
    probe = next(p for p in api_monitor.auth_probes('2026-11-10') if p['name'] == 'userstats')
    result = api_monitor.run_probe(probe, fake_fetch({}), {}, '2026-11-10')
    assert result['ok']
    assert 'skipped' in result['detail']


def test_run_monitor_all_ok_exit_0():
    code, report = api_monitor.run_monitor('public', public_fetch=public_ok_fetch(), today='2026-11-10')
    assert code == 0
    assert report['ok']
    assert len(report['results']) == len(api_monitor.PUBLIC_PROBES)


def test_run_monitor_failure_exit_1():
    failing = fake_fetch(
        {
            'sign-in': (200, b'{"json": true}'),
            'socialProfile': (401, b'{}'),
        }
    )
    code, report = api_monitor.run_monitor('public', public_fetch=failing, today='2026-11-10')
    assert code == 1
    assert report['problems'][0]['name'] == 'sso-signin-page'


def test_run_monitor_auth_unavailable_exit_2():
    code, report = api_monitor.run_monitor('auth', auth_fetch=None, today='2026-11-10')
    assert code == 2
    assert report['auth_unavailable']


def test_run_monitor_login_error_exit_1():
    code, report = api_monitor.run_monitor(
        'all', public_fetch=public_ok_fetch(), auth_fetch=None, login_error='Garmin SSO login failed: 429', today='2026-11-10'
    )
    assert code == 1
    assert report['problems'][0]['name'] == 'login'


def test_render_issue_body_mentions_probe():
    _, report = api_monitor.run_monitor('auth', auth_fetch=None, login_error='boom', today='2026-11-10')
    body = api_monitor.render_issue_body(report)
    assert 'login' in body
    assert 'boom' in body


def test_github_notify_dry_run_does_not_call_api():
    _, report = api_monitor.run_monitor('auth', auth_fetch=None, login_error='boom', today='2026-11-10')
    api_monitor.github_notify('DjAgge/garminimport', 'fake-token', report, dry_run=True)


def doc_probe():
    return {'name': 'gc-health-api', 'url': 'https://developer.garmin.com/x', 'kind': 'doc'}


def test_normalize_doc_strips_scripts_styles_and_tags():
    body = (
        b'<html><head><style>.x{color:red}</style><script>var a=1;</script><title>T &amp; Co</title></head>'
        b'<body><h1>Hello</h1> World</body></html>'
    )
    text = api_monitor.normalize_doc(body)
    assert 'Hello' in text and 'World' in text
    assert 'var a' not in text and 'color:red' not in text
    assert api_monitor.page_title(body) == 'T & Co'


def test_doc_hash_ignores_formatting_but_not_content():
    assert api_monitor.doc_hash(b'<html><body>v1</body></html>') == api_monitor.doc_hash(b'<html><body>  v1\n</body></html>')
    assert api_monitor.doc_hash(b'<html><body>v1</body></html>') != api_monitor.doc_hash(b'<html><body>v2</body></html>')


def test_run_probe_doc_ok_with_matching_baseline():
    body = b'<html><head><title>Health API</title></head><body>docs</body></html>'
    baseline = {
        'gc-health-api': {'url': 'https://developer.garmin.com/x', 'sha256': api_monitor.doc_hash(body), 'title': 'Health API'}
    }
    result = api_monitor.run_probe(
        doc_probe(), fake_fetch({'developer.garmin.com/x': (200, body)}), {}, '2026-11-10', docs_baseline=baseline
    )
    assert result['ok']
    assert result['sha256'] == baseline['gc-health-api']['sha256']
    assert result['title'] == 'Health API'


def test_run_probe_doc_change_detected():
    baseline = {'gc-health-api': {'sha256': '0' * 64}}
    result = api_monitor.run_probe(
        doc_probe(),
        fake_fetch({'developer.garmin.com/x': (200, b'<html><body>new</body></html>')}),
        {},
        '2026-11-10',
        docs_baseline=baseline,
    )
    assert not result['ok']
    assert 'documentation changed' in result['detail']
    assert 'sha256' in result


def test_run_probe_doc_missing_baseline_is_ok():
    result = api_monitor.run_probe(
        doc_probe(),
        fake_fetch({'developer.garmin.com/x': (200, b'<html><body>x</body></html>')}),
        {},
        '2026-11-10',
        docs_baseline={},
    )
    assert result['ok']
    assert 'no baseline' in result['detail']


def test_run_probe_doc_http_error():
    result = api_monitor.run_probe(
        doc_probe(), fake_fetch({'developer.garmin.com/x': (404, b'nope')}), {}, '2026-11-10', docs_baseline={}
    )
    assert not result['ok']
    assert 'HTTP 404' in result['detail']


def test_normalize_probe_set():
    assert api_monitor.normalize_probe_set('all') == frozenset({'public', 'auth', 'docs'})
    assert api_monitor.normalize_probe_set('public,docs') == frozenset({'public', 'docs'})
    assert api_monitor.normalize_probe_set('docs') == frozenset({'docs'})
    for bogus in ('bogus', ''):
        try:
            api_monitor.normalize_probe_set(bogus)
            raise AssertionError('expected ValueError')
        except ValueError:
            pass


def test_run_monitor_docs_ok_missing_and_failing():
    body = b'<html><head><title>D</title></head><body>docs</body></html>'
    fetch = fake_fetch({'developer.garmin.com': (200, body)})
    baseline = {p['name']: {'sha256': api_monitor.doc_hash(body)} for p in api_monitor.DOC_PROBES}
    code, report = api_monitor.run_monitor('docs', public_fetch=fetch, docs_baseline=baseline, today='2026-11-10')
    assert code == 0 and report['ok'] and len(report['results']) == len(api_monitor.DOC_PROBES)
    code, report = api_monitor.run_monitor('docs', public_fetch=fetch, docs_baseline={}, today='2026-11-10')
    assert code == 0 and all('no baseline' in r['detail'] for r in report['results'])
    code, report = api_monitor.run_monitor(
        'docs', public_fetch=fake_fetch({'developer.garmin.com': (500, b'err')}), docs_baseline=baseline, today='2026-11-10'
    )
    assert code == 1 and not report['ok']


def test_docs_baseline_roundtrip(tmp_path):
    path = str(tmp_path / 'baseline.json')
    api_monitor.save_docs_baseline({'a': {'sha256': 'x'}}, path=path)
    assert api_monitor.load_docs_baseline(path) == {'a': {'sha256': 'x'}}
    assert api_monitor.load_docs_baseline(str(tmp_path / 'missing.json')) == {}


def auth_ok_fetch(overrides=None):
    with open(f'{api_monitor.JSON_DIR}/activities-list.json') as f:
        activity = json.load(f)[0]
    with open(f'{api_monitor.JSON_DIR}/userstats.json') as f:
        userstats = json.load(f)
    props = {
        sample: '\n'.join(f'{key}=x' for key in api_monitor.sample_property_keys(sample))
        for sample in ('activity_types.properties', 'event_types.properties')
    }
    mapping = {
        'socialProfile': (200, json.dumps({'id': 1, 'displayName': 'tester'}).encode()),
        'activitylist-service': (200, json.dumps([activity]).encode()),
        'userstats-service': (200, json.dumps(userstats).encode()),
        'dailysummaries': (200, b'{"data": [{"calendarDate": "2026-11-10"}]}'),
        'dailySleepData': (200, b'{}'),
        'bodyBattery': (200, b'[]'),
        'hrv-service': (200, b'{}'),
        'weight/range': (200, b'[]'),
        'activity_types': (200, props['activity_types.properties'].encode()),
        'event_types': (200, props['event_types.properties'].encode()),
    }
    mapping.update(overrides or {})
    return fake_fetch(mapping)


def test_properties_probes_are_authenticated_not_public():
    public_names = [p['name'] for p in api_monitor.PUBLIC_PROBES]
    auth_names = [p['name'] for p in api_monitor.auth_probes('2026-11-10')]
    assert 'activity-types-properties' not in public_names
    assert 'event-types-properties' not in public_names
    assert 'activity-types-properties' in auth_names
    assert 'event-types-properties' in auth_names


def test_run_monitor_auth_ok_exit_0():
    code, report = api_monitor.run_monitor('auth', auth_fetch=auth_ok_fetch(), today='2026-11-10')
    assert code == 0
    assert report['ok']
    assert len(report['results']) == len(api_monitor.auth_probes('2026-11-10'))


def test_run_monitor_auth_properties_html_fails():
    fetch = auth_ok_fetch({'activity_types': (200, b'<html>sign in</html>')})
    code, report = api_monitor.run_monitor('auth', auth_fetch=fetch, today='2026-11-10')
    assert code == 1
    assert report['problems'][0]['name'] == 'activity-types-properties'
    assert 'HTML' in report['problems'][0]['detail']


def test_sample_keys_missing_file_returns_empty_set():
    assert api_monitor.sample_keys('does-not-exist.json') == set()
    assert api_monitor.sample_property_keys('does-not-exist.properties') == set()
