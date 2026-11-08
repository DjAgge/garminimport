FROM python:3.13-slim

WORKDIR /app

# Install deps first to improve caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gcexport.py garmin_tls_patch.py filtering.py ./
COPY csv_header_*.properties ./
COPY tools/ tools/

# One-shot container: runs gcexport.py (and optionally the Budibase import
# via tools/run_daily.sh), so the host/NAS scheduler decides when it runs.
# Runs as root on purpose: the compose stack writes to the named volume
# /data, which is root-owned on its first mount.
ENTRYPOINT ["python3", "gcexport.py"]
