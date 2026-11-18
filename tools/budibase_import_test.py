# -*- coding: utf-8 -*-
"""
Tests for tools/budibase_import.py; Call them with this command line:

py.test tools/budibase_import_test.py
"""

import json

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
