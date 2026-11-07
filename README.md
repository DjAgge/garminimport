# garmin-connect-export script

**This repository is a fork of [pe-st/garmin-connect-export](https://github.com/pe-st/garmin-connect-export)**
(see [Forks and Branches](#forks-and-branches) below for the full fork history).

It is maintained by [DjAgge](https://github.com/DjAgge) and was brought back up to date with the help of an
AI coding agent ([Vibe Code](https://mistral.ai), built by Mistral AI): all changes in this fork were developed
and tested live against Garmin Connect in a supervised AI session.

## What's different in this fork

The upstream script no longer worked against Garmin Connect at all (Garmin now rejects non-browser TLS
fingerprints and has moved all endpoints). This fork fixes that and adds health data export, token caching
and a Docker stack for scheduled runs:

- **Modern SSO/OAuth login** with browser TLS imitation via
  [curl_cffi](https://github.com/lexiforest/curl_cffi) (new runtime dependency), including support for
  accounts with **multi-factor authentication (MFA)**
- Hand-built OAuth1 signing that matches oauthlib exactly (an empty `oauth_token` must be omitted,
  otherwise Garmin answers with HTTP 401)
- All API endpoints moved to `connectapi.garmin.com` (the old `connect.garmin.com/modern/proxy/...`
  URLs answer with HTML)
- **Token caching** (`--tokenfile`, default `~/.gcexport_tokens.json`): later runs skip the login and
  thus the MFA prompt; expired access tokens are renewed from the OAuth1 token
- **Health data export** (`--health DAYS`): daily summaries, steps, stress, intensity minutes,
  hydration, sleep (score and detailed data), heart rate variability, weight and body battery as JSON
  files
- **Docker stack** (`Dockerfile`, `docker-compose.yml`, `tools/`): self-contained container with a
  built-in daily scheduler for NAS/Portainer operation
- **Budibase import** (`tools/budibase_import.py`): pushes the exported health data into the internal
  database of a self-hosted [Budibase](https://budibase.com) instance — one combined row per day
- Error messages now include Garmin's response body (status code and text) for much easier diagnosis

See [CHANGELOG.md](./CHANGELOG.md) for the detailed list of changes.


:exclamation: | This script [requires Python 3.8 or newer](https://github.com/pe-st/garmin-connect-export/issues/64)
---|---

:exclamation: | There is a [report of a deactivated user account that might by caused by using this script](https://github.com/pe-st/garmin-connect-export/issues/60). The exact reasons are not known, and the upstream author's account has never been deactivated. But be aware that no guarantee can be given that Garmin tolerates requests made from this script. The script is believed to be fair use (it doesn't do anything other than automating stuff that you do in the browser). But be careful if you plan to run the script as periodical task (with `cron` etc)
---|---

Download a copy of your personal Garmin Connect data: activity records and GPX/TCX/original/JSON files,
plus your daily health data (steps, stress, sleep, HRV, weight, ...).

Note that Garmin introduced a while ago (around May 2018, for GDPR compatibility) a possibility to
[download all of your Garmin Connect data](https://www.garmin.com/en-US/account/datamanagement/exportdata/)
in one zip file. Depending on your needs this might be enough, but the script here offers additional
features like getting GPX tracks instead of the original upload format, limiting the export to just a
couple of activities, incrementally skipping already downloaded files, and the health data export.

## Forks and Branches

Before going into the details of this script itself, some meta information.

There exist many [forks](https://help.github.com/articles/fork-a-repo/) of this script repository:

- [DjAgge](https://github.com/DjAgge/garminimport)
  This is the repository you're looking at: a fork of **pe-st**'s repo (see below), updated for the
  current Garmin Connect login and API (see [What's different in this fork](#whats-different-in-this-fork)).
- [pe-st](https://github.com/pe-st/garmin-connect-export)
  The upstream of this fork, **Peter Steiner**'s repository. It was
  [detached from **kjkjava**'s repo](https://github.com/pe-st/garmin-connect-export/issues/53)
  (see below) in February 2021, after more than 5 years of inactivity of the upstream repo.
- [kjkjava](https://github.com/kjkjava/garmin-connect-export)
  The original repo (mother repo) of **pe-st**'s repo. It seems not maintained anymore (last commit in 2015, see also: [pr#42](https://github.com/kjkjava/garmin-connect-export/pull/42) and [issues#46](https://github.com/kjkjava/garmin-connect-export/issues/46))
- [moderation](https://github.com/moderation/garmin-connect-export)
  After some inactivity of the **@kjkjava** repo, **@moderation** made some corrections in his own fork to have a working script again. His fork is primarily designed for his use which is cycling, while **pe-st**'s is running.
  In March 2018 **pe-st** integrated **@moderation**'s work into his own repo, so logically **@moderation** is now the
  father repo of **pe-st**'s repo. In April 2018 **@moderation** migrated his script to Python 3. Unfortunately
  **@moderation**'s script [didn't work for **pe-st** for a couple of months](https://github.com/moderation/garmin-connect-export/issues/11),
  probably because of different Garmin Connect REST endpoints (URLs).

For the [branches](https://git-scm.com/book/en/v2/Git-Branching-Basic-Branching-and-Merging) in **pe-st**'s repo see [BRANCH.md](BRANCH.md)

## Description

This script will backup your personal Garmin Connect data. All downloaded data will go into a directory
called `YYYY-MM-DD_garmin_connect_export/` in the current working directory (or the directory given
with `-d`). Activity records and details will go into a CSV file called `activities.csv`. GPX files
(or whatever format you specify) containing track data, activity title, and activity descriptions are
saved as well, using the Activity ID.

If there is no GPS track data (e.g., due to an indoor treadmill workout), a data file is still saved. If the GPX format is used, activity title and description data are saved. If the original format is used, Garmin may not provide a file at all and an empty file will be created. For activities where a GPX file was uploaded, Garmin may not have a TCX file available for download, so an empty file will be created. Since GPX is the only format Garmin should have for every activity, it is the default and preferred download format.

Already downloaded files are skipped on repeated runs, so the script can simply be run again after an
interruption (e.g., an "Operation timed out" message with many activities) and will pick up where it
left off.

With `--health DAYS` the daily health data is exported as JSON files into the subdirectory `health/` of
the output directory (see [Health Data](#health-data)).

## Installation

- If you're comfortable using Git, just clone the repo from github:
  `git clone https://github.com/DjAgge/garminimport.git`
- Otherwise get the latest `zip` (or `tar.gz`) from the [releases page](https://github.com/DjAgge/garminimport/releases)
  and unpack it where it suits you.
- The login requires the Python package [curl_cffi](https://github.com/lexiforest/curl_cffi)
  (see [below](#python-and-curl_cffi)): `pip install curl_cffi`
- On Debian/Ubuntu with Python 3.12+ `pip` refuses system-wide installations (PEP 668); the clean way
  is a virtual environment:

  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  pip install curl_cffi
  ```

## Usage

You will need a little experience running things from the command line to use this script. That said, here are the usage details from the `--help` flag:

```
usage: gcexport.py [-h] [--version] [-v] [--username USERNAME]
                   [--password PASSWORD] [-c COUNT] [-e EXTERNAL] [-a ARGS]
                   [-f {gpx,tcx,original,json}] [-d DIRECTORY] [-s SUBDIR]
                   [-lp LOGPATH] [-u] [-ot] [--desc [DESC]] [-t TEMPLATE]
                   [-fp] [--tokenfile TOKENFILE] [--health HEALTH]
                   [-sa START_ACTIVITY_NO] [-ex FILE]

Garmin Connect Exporter

options:
  -h, --help            show this help message and exit
  --version             print version and exit
  -v, --verbosity       increase output and log verbosity, save more
                        intermediate files
  --username USERNAME   your Garmin Connect username or email address
                        (otherwise, you will be prompted)
  --password PASSWORD   your Garmin Connect password (otherwise, you will be
                        prompted)
  -c COUNT, --count COUNT
                        number of recent activities to download, or 'all'
                        (default: 1)
  -e EXTERNAL, --external EXTERNAL
                        path to external program to pass CSV file too
  -a ARGS, --args ARGS  additional arguments to pass to external program
  -f {gpx,tcx,original,json}, --format {gpx,tcx,original,json}
                        export format; can be 'gpx', 'tcx', 'original' or
                        'json' (default: 'gpx')
  -d DIRECTORY, --directory DIRECTORY
                        the directory to export to (default: './YYYY-MM-
                        DD_garmin_connect_export')
  -s SUBDIR, --subdir SUBDIR
                        the subdirectory for activity files (tcx, gpx etc.),
                        supported placeholders are {YYYY} and {MM} (default:
                        export directory)
  -lp LOGPATH, --logpath LOGPATH
                        the directory to store logfiles (default: same as for
                        --directory)
  -u, --unzip           if downloading ZIP files (format: 'original'), unzip
                        the file and remove the ZIP file
  -ot, --originaltime   will set downloaded (and possibly unzipped) file time
                        to the activity start time
  --desc [DESC]         append the activity's description to the file name of
                        the download; limit size if number is given
  -t TEMPLATE, --template TEMPLATE
                        template file with desired columns for CSV output
  -fp, --fileprefix     set the local time as activity file name prefix
  --tokenfile TOKENFILE
                        JSON file for caching the OAuth tokens, so that later
                        runs skip the login (and the MFA prompt). Set to an
                        empty string to disable the token file
  --health HEALTH       export the daily health data (steps, stress, sleep,
                        HRV, weight, ...) of the last HEALTH days as JSON
                        files into the subdirectory "health" of the output
                        directory
  -sa START_ACTIVITY_NO, --start_activity_no START_ACTIVITY_NO
                        give index for first activity to import, i.e. skipping
                        the newest activities
  -ex FILE, --exclude FILE
                        JSON file with Array of activity IDs to exclude from
                        download. Format example: {"ids": ["6176888711"]}
```

### Examples

- `python gcexport.py --count all`
  will download all of your activities to a dated directory.

- `python gcexport.py -c all -f gpx -ot --desc 20`
  will export all of your activities in GPX format, set the timestamp of the GPX files to the start time of the activity and append the 20 first characters of the activity's description to the file name.

- `python gcexport.py -c all -e /Applications/LibreOffice.app/Contents/MacOS/soffice -a calc`
  will download all of your data and then use LibreOffice to open the CSV file with the list of your activities (the path to LibreOffice is platform-specific; the example is for macOS).

- `python gcexport.py -d ~/MyActivities -c 3 -f original -u --username bobbyjoe --password bestpasswordever1`
  will download your three most recent activities in the FIT file format (or whatever they were uploaded as) into the `~/MyActivities` directory (unless they already exist). Using the `--password` flags is not recommended because your password will be stored in your command line history. Instead, omit it to be prompted (and note that nothing will be displayed when you type your password). Equally you might not want to have the username stored in your command line history; in this case avoid also to give the `--username` option, and you'll be prompted for it. Note also that depending on the age of your garmin account your username is the email address (for some users the email address is mandatory to login).

- `python gcexport.py -c 5 -f gpx -v --health 30`
  will export your five most recent activities as GPX files and the health data of the last 30 days
  into the `health/` subdirectory. The first run asks for password (and MFA code if required); later
  runs reuse the cached tokens without any prompt.

Alternatively, you may run it with `./gcexport.py` if you set the file as executable (i.e., `chmod u+x gcexport.py`).

### Login, MFA and Token Caching

The login uses Garmin's modern SSO/OAuth flow with browser TLS imitation (see
[Python and curl_cffi](#python-and-curl_cffi)). If your account requires multi-factor authentication,
the script prompts for the MFA code (`MFA code (email|phone):`) when Garmin sends one.

The OAuth tokens are cached in `~/.gcexport_tokens.json` (change with
`--tokenfile`, disable with `--tokenfile ""`). Later runs reuse the cached
session: a still-valid access token is used directly, an expired one is
renewed from the OAuth1 token — both without an SSO login and thus without
an MFA prompt. A full login (with MFA) is only needed when the tokens are
absent or completely expired. The token file is stored with owner-only
permissions; for scheduled/Docker runs it should live on a persistent volume.

Note that Garmin rate limits repeated full logins (HTTP 429): avoid running
many logins in a short time; with token caching this is rarely an issue.

### Health Data

With `--health DAYS` the daily health data of the last DAYS days is exported
as JSON files into the subdirectory `health` of the output directory:

- `steps.json`, `stress.json`, `intensity_minutes.json`, `hydration.json`,
  `sleep_score.json`, `hrv.json`, `weight.json` — one file per category for the
  whole date range (requests are automatically chunked to Garmin's 28-day
  limit)
- `daily/<date>_daily_summary.json`, `daily/<date>_sleep.json`,
  `daily/<date>_body_battery.json` — one file per day

Services that are unavailable or not used (e.g., no smart scale) are skipped
with a warning. `--health 0` (the default) disables the health export.

### Docker / NAS / Portainer (daily scheduled export)

The repository ships a `Dockerfile` and a `docker-compose.yml` for running the
export on a NAS. The stack consists of one long-lived container that triggers
the export once a day (default 06:00, `RUN_TIME`) and then runs the Budibase
import. The OAuth tokens and the exports live on the named volume
`garmin-data`, so later runs need no login and no MFA code.

**Deploy with Portainer:** Stacks → Add stack → Repository, use this repository
with the compose path `docker-compose.yml`, and set the environment variables
(`BUDIBASE_URL`, `BUDIBASE_API_KEY`, optionally `RUN_TIME`, `TZ`, `HEALTH_DAYS`)
in the stack's "Environment variables" section. Deploy the stack.

**First login (once, interactive password + MFA):** open the console of the
`garmin-scheduler` container in Portainer (or `docker exec -it <container> sh`)
and run:

```sh
python3 gcexport.py --username <email> --tokenfile /data/tokens.json -d /data/export --health 60
```

This also fetches the last 60 days of health data as the initial history
(the 60 days then end up in Budibase as history). Alternatively you can copy an
existing token file from another machine into the volume.

**Manual runs** (also from the container console):

```sh
HEALTH_DAYS=60 sh tools/run_daily.sh   # e.g. fetch more history once
sh tools/run_daily.sh                  # normal run like the daily schedule
```

The daily schedule runs `tools/run_daily.sh` via `tools/run_loop.sh`: it
exports the activities and the last `HEALTH_DAYS` days (default 2) of health
data and then runs the Budibase import (see below) if `BUDIBASE_API_KEY` is
configured. A failed run is logged and retried the next day.

### Budibase Import (health data)

`tools/budibase_import.py` pushes the exported health data into the internal
database of a self-hosted [Budibase](https://budibase.com) instance via its
REST API:

- everything is combined into **one row per calendar day** in one table
  ("Health Daily"), so the data is uniformly day-based
- included per day: the daily summary (steps, calories, resting heart rate,
  body battery, ...), stress, intensity minutes, hydration, sleep score, HRV
  and **at most one weight measurement per day** (the last one of the day)
- fields from the range statistics are prefixed with their source (e.g.
  `stress_avg`, `sleepScore_value`, `hrv_lastNightAvg`), weight fields with
  `weight_` (e.g. `weight_weight`, `weight_bmi`)
- the table and its columns are created automatically if they do not exist
- rows carry an `importKey` (`daily:<date>`); repeated runs never import
  duplicates

Configuration is done via environment variables (`BUDIBASE_URL`,
`BUDIBASE_API_KEY`, `EXPORT_DIR`, see the script header for all options); set
`DRY_RUN=1` to preview what would be imported. In `docker-compose.yml` the
import runs automatically after the export; leave `BUDIBASE_API_KEY` empty to
disable it.

### Notes on the Usage

- The `-c COUNT` option might appear to count wrongly when exporting multi-sport activities;
  they count as one activity, but the incrementing counter displayed on the console counts
  also the individual parts of a multi-sport activity

### Python and curl_cffi

Of course, you must have Python installed to run this, any recent 3.x version should work
(see also [Python 3.x Versions](./CONTRIBUTING.md#python-3x-versions)).

The login to Garmin Connect requires the [curl_cffi](https://github.com/lexiforest/curl_cffi)
Python package (`pip install curl_cffi`): Garmin's SSO endpoints reject requests whose
TLS fingerprint does not look like a real browser, and curl_cffi provides that browser
TLS imitation. Login with multi-factor authentication (MFA) is supported; the script
prompts for the MFA code when Garmin requires one.

Most Mac and Linux users should already have Python. On Debian/Ubuntu with Python 3.12+
use a virtual environment (see [Installation](#installation)).

Also, as stated above, you should have some basic command line experience.


## Data

This tool is not guaranteed to get all of your data, or even download it correctly. It has been tested
with the maintainer's account and works fine, but different account settings or different data types
could potentially cause problems. Also, because this is not an official feature of Garmin Connect,
Garmin may very well make changes that break this utility (and they certainly have since this project
was created).

If you want to see all the raw data that Garmin hands to this script, just choose the JSON export format (`-f json`); in this case only metadata is exported, no track data. With `--verbosity` (or `-v`) the script additionally saves intermediate files (e.g., the raw activity list and the profile data) for inspection.

The format of the CSV export file can be customized with template files (in Properties format, see the `--template` option); three examples are included:

- `csv_header_default.properties` (the default) gives you the maintainer's preferred selection of columns, mainly targeted at running and hiking
- `csv_header_all.properties` gives you all available columns, handy as starting point for your own selection
- `csv_header_moderation.properties` gives you the same output as **@moderation**'s fork, mainly targeted at cycling
- `csv_header_kjkjava.properties` gives you an output similar as **@kjkjava**'s original script, mainly targeted at running

You can easily create a template file for your needs, just copy one of the examples and change the appearing columns, their order and/or their title.

Some important columns explained:

- `raw` (e.g. `durationRaw`) columns usually give you unformatted data as provided by the Garmin API, other columns (e.g. `duration`) often format the data more readable
- speed columns (e.g. `averageSpeedRaw` and `averageSpeedPace`): when there is `Pace` in the column name the value given is a speed (km/) or pace (minutes per kilometer) depending on the activity type (e.g. pace for running, hiking and walking activities, speed for other activities)
- The elevation is either uncorrected or corrected, with a flag telling which. The current API doesn't provide both sets of elevations

## Garmin Connect API

This script is for personal use only. It simulates a standard user session (i.e., in the browser):
the login goes through Garmin's SSO (with browser TLS imitation), the API requests carry an OAuth2
bearer token obtained via Garmin's OAuth endpoints. This makes the script pretty brittle — when
Garmin changes its login or endpoints, the script needs to be adapted. If you're looking for a more
reliable option, particularly if you wish to use this for some production service, Garmin does offer
a paid API service.

More information about the API endpoints used in the script is available in [CONTRIBUTING.md](CONTRIBUTING.md)

## History

The original project was written in PHP (formerly in the `old` directory, now deleted), based on "Garmin Connect export to Dailymile" code at <http://www.ciscomonkey.net/gc-to-dm-export/> (link has been down for a while). It no longer works due to the way Garmin handles logins. It could be updated, but **kjkjava** decided to rewrite everything in Python for the latest version.

After 2015, when the original repo stopped being maintained, several forks from **kjkjava** started appearing (see
Forks and Branches section above).

In 2021 **pe-st**'s repository was [detached from the original repo](https://github.com/pe-st/garmin-connect-export/issues/53);
in what concerns Github, the repo isn't a fork anymore, but a new "original".
This repository in turn is a fork of **pe-st**'s repo, updated for the current Garmin Connect
(see [What's different in this fork](#whats-different-in-this-fork)).
For the history of this fork see the [CHANGELOG](CHANGELOG.md)


## Contributions

Contributions are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md)

Contributors as of 2021-12 (Hope I didn't forget anyone,
see also [Contributors](https://github.com/pe-st/garmin-connect-export/graphs/contributors)):

- Kyle Krafka @kjkjava
- Jochem Wichers Hoeth @jowiho
- Andreas Loeffler @lefty01
- @sclub
- Yohann Coppel @yohcop
- Tobias Ljunggren @tobiaslj
- @cdstrachan
- Michael Payne @moderation
- Chris McCarty @cmccarty
- Julien Rebetez @julienr
- Peter Steiner @pe-st
- @lindback
- @TheKiteRunning
- Jens Diemer @jedie
- Christian Moelders @chmoelders
- Christian Schulzendorff @chs8691
- Josef K @jkall
- Thomas Th @telemaxx
- Bart Skowron @bxsx
- @reto
- Cristian @cristian5th
- @hannesweisbach

## License

[MIT](./LICENSE) &copy; 2015 Kyle Krafka and contributors
