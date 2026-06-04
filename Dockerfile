# finbot deployment image — runs the unattended daily job.
FROM python:3.11-slim

# libgomp1: required by lightgbm. tzdata: so the trading-day/cron logic uses CST.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY . /app

# Core package + real data source + model + parquet + Claude SDK (agent layer).
RUN pip install -e . \
    && pip install akshare lightgbm pyarrow anthropic

# The daily job: trading-day guard -> update -> run -> (LLM) -> notify.
ENTRYPOINT ["python", "scripts/daily_job.py"]
