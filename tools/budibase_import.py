#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
File: tools/budibase_import.py

Description:    Imports the health data exported by gcexport.py --health into
                the internal database of a self-hosted Budibase instance via
                its REST API (v1).

                The importer is deliberately kept separate from gcexport.py:
                it reads the JSON files produced by an export run and can be
                run afterwards (see tools/run_daily.sh), so the export
                itself stays independent of any database.

                Everything is combined into ONE row per calendar day in one
                table: the daily summary (steps, calories, resting heart
                rate, body battery, ...) plus the range statistics (stress,
                intensity minutes, hydration, sleep score, HRV) plus at most
                ONE weight measurement per day (the last one of the day).
                Range fields are prefixed with their source (e.g. "stress_"),
                weight fields with "weight_".

Configuration via environment variables:

    BUDIBASE_URL          base URL of the Budibase instance, e.g.
                          http://budibase:8080 or https://bb.example.com
    BUDIBASE_API_KEY      API key of the Budibase user (user menu → API key)
    BUDIBASE_APP_ID       ID of the Budibase app/workspace the table lives in
                          (from the builder URL, /builder/workspace/<id>/);
                          use the published ID (without "_dev_") for
                          published apps, e.g. app_641843cd1c254cf096fb059a68f32405
    BUDIBASE_TABLE        table for the daily rows (default "Health Daily")
    EXPORT_DIR            gcexport output directory containing health/
    DRY_RUN               set to 1 to only print what would be imported

The table is created automatically if it does not exist yet (columns are
inferred from the JSON data). Rows are identified by an 'importKey' column so
that repeated daily runs never create duplicates.
"""

import json
import logging
import os
import sys
from glob import glob

try:
    from curl_cffi import requests as http
except ImportError:  # pragma: no cover
    sys.exit('curl_cffi is required (pip install curl_cffi)')

BUDIBASE_URL = os.environ.get('BUDIBASE_URL', '').rstrip('/')
BUDIBASE_API_KEY = os.environ.get('BUDIBASE_API_KEY', '')
APP_ID = os.environ.get('BUDIBASE_APP_ID', '')
TABLE = os.environ.get('BUDIBASE_TABLE', 'Health Daily')
EXPORT_DIR = os.environ.get('EXPORT_DIR', '.')
DRY_RUN = os.environ.get('DRY_RUN', '') == '1'

SCALAR_TYPES = (str, int, float, bool)

# range JSONs of the export, with the prefix their fields get in the daily row
RANGE_FILES = {
    'stress_': 'stress.json',
    'im_': 'intensity_minutes.json',
    'hydration_': 'hydration.json',
    'sleepScore_': 'sleep_score.json',
    'hrv_': 'hrv.json',
}


class BudibaseException(Exception):
    pass


def api(method, path, json_body=None):
    """Call the Budibase v1 REST API and return the parsed JSON response."""
    resp = http.request(
        method,
        f'{BUDIBASE_URL}/api/public/v1{path}',
        headers={'x-budibase-api-key': BUDIBASE_API_KEY, 'x-budibase-app-id': APP_ID},
        json=json_body,
    )
    if resp.status_code not in (200, 201):
        raise BudibaseException(f'{method} {path} failed: {resp.status_code} {resp.text}')
    return resp.json() if resp.text else {}


def ensure_table(table_name, rows):
    """
    Return the id of *table_name*, creating the table with a schema inferred
    from *rows* if it does not exist yet.
    """
    if DRY_RUN:
        return f'dry-run-{table_name}'
    result = api('POST', '/tables/search', {'name': table_name})
    for table in result.get('data', []):
        if table['name'] == table_name:
            return table['_id']

    schema = {}
    for row in rows:
        for key, value in row.items():
            if key not in schema:
                if isinstance(value, bool):
                    schema[key] = {'type': 'boolean', 'name': key}
                elif isinstance(value, (int, float)):
                    schema[key] = {'type': 'number', 'name': key}
                else:
                    schema[key] = {'type': 'string', 'name': key}
    logging.info('Creating Budibase table %s with %d columns', table_name, len(schema))
    return api('POST', '/tables', {'name': table_name, 'schema': schema})['data']['_id']


def existing_import_keys(table_id):
    """Return the set of 'importKey' values already present in the table."""
    keys = set()
    search_after = None
    while True:
        body = {'query': {}, 'limit': 1000}
        if search_after:
            body['bookmark'] = search_after
        result = api('POST', f'/tables/{table_id}/rows/search', body)
        for row in result.get('data', []):
            if 'importKey' in row:
                keys.add(row['importKey'])
        if not result.get('hasNextPage'):
            return keys
        search_after = result.get('bookmark')


def import_rows(table_name, rows):
    """Import *rows* into *table_name*, skipping rows whose importKey exists."""
    if not rows:
        logging.info('No rows to import into %s', table_name)
        return
    table_id = ensure_table(table_name, rows)
    existing = existing_import_keys(table_id) if not DRY_RUN else set()
    new_rows = [row for row in rows if row['importKey'] not in existing]
    logging.info('Importing %d of %d rows into %s', len(new_rows), len(rows), table_name)
    for row in new_rows:
        if DRY_RUN:
            logging.info('Dry run, would import: %s', row['importKey'])
        else:
            api('POST', f'/tables/{table_id}/rows', row)


def add_prefixed_scalars(target, prefix, data):
    """Add the scalar fields of *data* to *target*, prefixed with *prefix*."""
    for key, value in data.items():
        if key == 'calendarDate':
            continue
        if isinstance(value, SCALAR_TYPES):
            target[f'{prefix}{key}'] = value
        elif isinstance(value, dict):
            add_prefixed_scalars(target, prefix, value)


def load_daily_summaries():
    """
    Read the per-day daily summary JSONs; returns {calendarDate: flat scalars}.
    """
    summaries = {}
    for filename in sorted(glob(os.path.join(EXPORT_DIR, 'health', 'daily', '*_daily_summary.json'))):
        with open(filename, encoding='utf-8') as summary_json:
            summary = json.load(summary_json)
        day = summary.get('calendarDate')
        if not day:
            continue
        flat = {key: value for key, value in summary.items() if isinstance(value, SCALAR_TYPES)}
        summaries[day] = flat
    return summaries


def _range_files(filename):
    """
    Yield every copy of a range JSON: the archived ones first (oldest first,
    named <filename>.<timestamp>.json by run_daily.sh), then the current
    file. Callers merge per calendarDate as they read, so newer copies
    overwrite older entries for the same day.
    """
    archived = sorted(glob(os.path.join(EXPORT_DIR, 'health', 'archive', f'{filename}.*.json')))
    current = os.path.join(EXPORT_DIR, 'health', filename)
    for path in archived:
        yield path
    if os.path.isfile(current):
        yield current


def load_range_entries(prefix, filename):
    """
    Read a range JSON (a list of per-day entries, or a dict with such a list)
    and return {calendarDate: prefixed scalar fields}. Archived copies from
    earlier export runs are merged in, so history that the daily runs no
    longer fetch (HEALTH_DAYS) is not lost.
    """
    entries = {}
    for path in _range_files(filename):
        with open(path, encoding='utf-8') as range_json:
            data = json.load(range_json)
        if isinstance(data, dict):
            data = data.get('hrvSummaries') or []
        for item in data:
            day = item.get('calendarDate')
            if not day:
                continue
            entry = {}
            add_prefixed_scalars(entry, prefix, item)
            entries[day] = entry
    return entries


# Garmin supplies these weight fields in grams; store them in kilograms
WEIGHT_GRAM_FIELDS = {'weight_weight', 'weight_weightDelta'}


def load_weight_per_day():
    """
    Read the weight range JSON and return {calendarDate: prefixed fields} with
    at most ONE measurement per day: the last one of that day. Archived copies
    from earlier export runs are merged in so the weight history survives
    daily runs with a small HEALTH_DAYS window.
    """
    latest = {}
    for path in _range_files('weight.json'):
        with open(path, encoding='utf-8') as weight_json:
            weight_data = json.load(weight_json)
        for summary in weight_data.get('dailyWeightSummaries', []):
            for measurement in summary.get('allWeightMetrics', []):
                day = measurement.get('calendarDate') or measurement.get('date')
                if not day:
                    continue
                stamp = measurement.get('datetimeUtc') or measurement.get('time') or ''
                if day not in latest or stamp >= latest[day][0]:
                    latest[day] = (stamp, measurement)
    entries = {}
    for day, (_, measurement) in latest.items():
        entry = {}
        add_prefixed_scalars(entry, 'weight_', measurement)
        for key in WEIGHT_GRAM_FIELDS:
            if key in entry and isinstance(entry[key], (int, float)):
                entry[key] = entry[key] / 1000
        entries[day] = entry
    return entries


def load_daily_rows():
    """Combine all health sources into one flat row per calendar day."""
    days = {}
    sources = [load_daily_summaries()]
    for prefix, filename in RANGE_FILES.items():
        sources.append(load_range_entries(prefix, filename))
    sources.append(load_weight_per_day())

    for source in sources:
        for day, fields in source.items():
            days.setdefault(day, {}).update(fields)
    for day, row in days.items():
        row.setdefault('calendarDate', day)
        row['importKey'] = f'daily:{day}'
    return [days[day] for day in sorted(days)]


def main():
    logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
    if not BUDIBASE_URL or not BUDIBASE_API_KEY:
        sys.exit('BUDIBASE_URL and BUDIBASE_API_KEY must be set')
    if not APP_ID:
        sys.exit('BUDIBASE_APP_ID must be set (from the builder URL, ' '/builder/workspace/<id>/)')
    if not os.path.isdir(os.path.join(EXPORT_DIR, 'health')):
        sys.exit(f'No health data found in {EXPORT_DIR!r} (run gcexport.py --health first)')

    import_rows(TABLE, load_daily_rows())
    logging.info('Budibase import finished')


if __name__ == '__main__':
    main()
