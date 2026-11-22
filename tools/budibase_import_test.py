# -*- coding: utf-8 -*-
"""
Tests for tools/budibase_import.py; Call them with this command line:

py.test tools/budibase_import_test.py
"""

import json

import pytest

import budibase_import


def test_flatten_daily_summary_v2_topics():
    summary = {
        'calendarDate': '2026-11-10',
        'movement': {'steps': {'value': 17556}, 'floorsClimbed': {'value': 12}},
        'calories': {'active': {'value': 552}, 'bmr': {'value': 1731}},
        'heartRate': {'restingValue': 57, 'minValue': 55, 'maxValue': 147},
        'stress': {'avgLevel': 50, 'maxLevel': 71},
        'bodyBattery': {'chargedValue': 22, 'drainedValue': 5},
        'pulseOx': {'averageSpO2': 96},
    }
    flat = budibase_import.flatten_daily_summary(summary)
    assert flat['calendarDate'] == '2026-11-10'
    assert flat['steps'] == 17556
    assert flat['floorsClimbed'] == 12
    assert flat['activeKilocalories'] == 552
    assert flat['bmrKilocalories'] == 1731
    assert flat['restingHeartRateInBeatsPerMinute'] == 57
    assert flat['minHeartRateInBeatsPerMinute'] == 55
    assert flat['maxHeartRateInBeatsPerMinute'] == 147
    assert flat['averageStressLevel'] == 50
    assert flat['maxStressLevel'] == 71
    assert flat['averageSpo2'] == 96
    assert flat['bodyBattery_chargedValue'] == 22
    assert flat['bodyBattery_drainedValue'] == 5


def test_flatten_daily_summary_v1_flat_passthrough():
    summary = {'calendarDate': '2026-10-27', 'steps': 9000, 'activeKilocalories': 400, 'averageSpo2': 97}
    assert budibase_import.flatten_daily_summary(summary) == summary


def test_load_daily_summaries_reads_v2_files(tmp_path, monkeypatch):
    daily = tmp_path / 'health' / 'daily'
    daily.mkdir(parents=True)
    (daily / '2026-11-10_daily_summary.json').write_text(
        json.dumps({'calendarDate': '2026-11-10', 'movement': {'steps': {'value': 17556}}, 'heartRate': {'restingValue': 57}}),
        encoding='utf-8',
    )
    monkeypatch.setattr(budibase_import, 'EXPORT_DIR', str(tmp_path))
    summaries = budibase_import.load_daily_summaries()
    assert summaries == {'2026-11-10': {'calendarDate': '2026-11-10', 'steps': 17556, 'restingHeartRateInBeatsPerMinute': 57}}


@pytest.fixture
def export_dir(tmp_path):
    """A fake gcexport --health output directory with one day of health data."""
    health = tmp_path / 'health'
    daily = health / 'daily'
    daily.mkdir(parents=True)
    (daily / '2026-11-18_daily_summary.json').write_text(
        json.dumps(
            {
                'calendarDate': '2026-11-18',
                'movement': {'steps': {'value': 12345}},
                'heartRate': {'restingValue': 55},
            }
        ),
        encoding='utf-8',
    )
    (health / 'stress.json').write_text(json.dumps([{'calendarDate': '2026-11-18', 'avgStressLevel': 42}]), encoding='utf-8')
    (health / 'hrv.json').write_text(
        json.dumps({'hrvSummaries': [{'calendarDate': '2026-11-18', 'lastNightAvg': 38}]}), encoding='utf-8'
    )
    (health / 'weight.json').write_text(
        json.dumps(
            {
                'dailyWeightSummaries': [
                    {
                        'calendarDate': '2026-11-18',
                        'allWeightMetrics': [
                            {
                                'calendarDate': '2026-11-18',
                                'datetimeUtc': '2026-11-18T06:00:00',
                                'weight': 75000,
                                'boneMass': 3200,
                                'muscleMass': 35000,
                            },
                        ],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )
    return tmp_path


def test_app_kind():
    assert budibase_import.app_kind('app_dev_abc123') == 'development'
    assert budibase_import.app_kind('app_abc123') == 'published'


def test_load_daily_rows_combines_all_sources(export_dir, monkeypatch):
    monkeypatch.setattr(budibase_import, 'EXPORT_DIR', str(export_dir))
    rows = budibase_import.load_daily_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row['calendarDate'] == '2026-11-18'
    assert row['steps'] == 12345
    assert row['restingHeartRateInBeatsPerMinute'] == 55
    assert row['stress_avgStressLevel'] == 42
    assert row['hrv_lastNightAvg'] == 38
    assert row['weight_weight'] == 75.0
    assert row['weight_boneMass'] == 3.2
    assert row['weight_muscleMass'] == 35.0
    assert row['importKey'] == 'daily:2026-11-18'


def test_import_rows_creates_table_and_posts_rows(export_dir, monkeypatch):
    calls = []
    tables = {}

    def fake_api(method, path, json_body=None):
        calls.append((method, path))
        if path == '/tables/search':
            return {'data': [t for t in tables.values() if t['name'] == json_body['name']]}
        if method == 'POST' and path == '/tables':
            table = {'_id': 'ta_1', 'name': json_body['name'], 'schema': json_body['schema']}
            tables[table['_id']] = table
            return {'data': table}
        if path == '/tables/ta_1/rows/search':
            return {'data': [], 'hasNextPage': False}
        if method == 'POST' and path == '/tables/ta_1/rows':
            return {'data': {'_id': 'ro_1', **json_body}}
        raise AssertionError(f'unexpected API call: {method} {path}')

    monkeypatch.setattr(budibase_import, 'api', fake_api)
    monkeypatch.setattr(budibase_import, 'EXPORT_DIR', str(export_dir))
    monkeypatch.setattr(budibase_import, 'DRY_RUN', False)
    budibase_import.import_rows('Health Daily', budibase_import.load_daily_rows())
    assert ('POST', '/tables') in calls
    assert ('POST', '/tables/ta_1/rows/search') in calls
    assert ('POST', '/tables/ta_1/rows') in calls


def test_main_exits_loudly_when_no_health_rows(tmp_path, monkeypatch):
    (tmp_path / 'health').mkdir()
    monkeypatch.setattr(budibase_import, 'EXPORT_DIR', str(tmp_path))
    monkeypatch.setattr(budibase_import, 'BUDIBASE_URL', 'http://budibase:10000')
    monkeypatch.setattr(budibase_import, 'BUDIBASE_API_KEY', 'key')
    monkeypatch.setattr(budibase_import, 'APP_ID', 'app_dev_xyz')
    with pytest.raises(SystemExit):
        budibase_import.main()
