FROM python:3.12-slim

RUN pip install --no-cache-dir curl_cffi

WORKDIR /app

COPY gcexport.py garmin_tls_patch.py filtering.py ./
COPY csv_header_*.properties ./
COPY tools/ tools/

# One-shot container: runs gcexport.py (and optionally the Budibase import
# via tools/run_daily.sh), so the host/NAS scheduler decides when it runs.
ENTRYPOINT ["python3", "gcexport.py"]
