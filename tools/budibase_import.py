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

Configuration via environment variables:

    BUDIBASE_URL          base URL of the Budibase instance, e.g.
                          http://budibase:8080 or https://bb.example.com
    BUDIBASE_API_KEY      API key of the Budibase app (Settings → API keys)
    BUDIBASE_TABLE_DAILY  table for the daily summaries (default "Health Daily")
    BUDIBASE_TABLE_WEIGHT table for the weight measurements (default "Health Weight")
    EXPORT_DIR            gcexport output directory containing health/
    DRY_RUN               set to 1 to only print what would be imported

The tables are created automatically if they do not exist yet (columns are
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
TABLE_DAILY = os.environ.get('BUDIBASE_TABLE_DAILY', 'Health Daily')
TABLE_WEIGHT = os.environ.get('BUDIBASE_TABLE_WEIGHT', 'Health Weight')
EXPORT_DIR = os.environ.get('EXPORT_DIR', '.')
DRY_RUN = os.environ.get('DRY_RUN', '') == '1'

SCALAR_TYPES = (str, int, float, bool)


class BudibaseException(Exception):
    pass


def api(method, path, json_body=None):
    """Call the Budibase v1 REST API and return the parsed JSON response."""
    resp = http.request(
        method,
        f'{BUDIBASE_URL}/api/v1{path}',
        headers={'x-budibase-api-key': BUDIBASE_API_KEY},
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
    for table in api('GET', '/tables').get('data', []):
        if table['name'] == table_name:
            return table['_id']

    schema = {}
    for row in rows:
        for key, value in row.items():
            if key not in schema:
                if isinstance(value, bool):
                    schema[key] = {'type': 'boolean'}
                elif isinstance(value, (int, float)):
                    schema[key] = {'type': 'number'}
                else:
                    schema[key] = {'type': 'string'}
    logging.info('Creating Budibase table %s with %d columns', table_name, len(schema))
    return api('POST', '/tables', {'name': table_name, 'schema': schema})['_id']


def existing_import_keys(table_id):
    """Return the set of 'importKey' values already present in the table."""
    keys = set()
    search_after = None
    while True:
        body = {'query': {}, 'limit': 1000}
        if search_after:
            body['bookmark'] = search_after
        result = api('POST', f'/tables/{table_id}/rows/search', body)
        for row in result.get('rows', []):
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


def flatten_scalars(data):
    """Keep only the top-level scalar fields of a JSON object."""
    return {key: value for key, value in data.items() if isinstance(value, SCALAR_TYPES)}


def load_daily_summaries():
    """Read the per-day daily summary JSONs into flat rows."""
    rows = []
    for filename in sorted(glob(os.path.join(EXPORT_DIR, 'health', 'daily', '*_daily_summary.json'))):
        with open(filename, encoding='utf-8') as summary_json:
            summary = json.load(summary_json)
        flat = flatten_scalars(summary)
        if not flat.get('calendarDate'):
            continue
        flat['importKey'] = f"daily:{flat['calendarDate']}"
        rows.append(flat)
    return rows


def load_weight_measurements():
    """Read the weight range JSON into one row per measurement."""
    rows = []
    filename = os.path.join(EXPORT_DIR, 'health', 'weight.json')
    if not os.path.isfile(filename):
        return rows
    with open(filename, encoding='utf-8') as weight_json:
        weight_data = json.load(weight_json)
    for summary in weight_data.get('dailyWeightSummaries', []):
        for measurement in summary.get('allWeightMetrics', []):
            flat = flatten_scalars(measurement)
            if not flat:
                continue
            key = f"weight:{flat.get('date', '')}:{flat.get('datetimeUtc', flat.get('time', ''))}"
            flat['importKey'] = key
            rows.append(flat)
    return rows


def main():
    logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
    if not BUDIBASE_URL or not BUDIBASE_API_KEY:
        sys.exit('BUDIBASE_URL and BUDIBASE_API_KEY must be set')
    if not os.path.isdir(os.path.join(EXPORT_DIR, 'health')):
        sys.exit(f'No health data found in {EXPORT_DIR!r} (run gcexport.py --health first)')

    import_rows(TABLE_DAILY, load_daily_summaries())
    import_rows(TABLE_WEIGHT, load_weight_measurements())
    logging.info('Budibase import finished')


if __name__ == '__main__':
    main()
