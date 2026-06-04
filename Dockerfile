# finbot deployment image — runs the unattended daily job.
FROM python:3.11-slim

# Domestic mirrors greatly speed up builds from mainland China. Override with
# --build-arg PIP_INDEX_URL=... (e.g. https://pypi.org/simple) outside China.
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# libgomp1: required by lightgbm. tzdata: so the trading-day/cron logic uses CST.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app
COPY . /app

# Core package + real data source + model + parquet + Claude SDK (agent layer).
RUN pip install --upgrade pip \
    && pip install -e . \
    && pip install akshare lightgbm pyarrow anthropic

# The daily job: trading-day guard -> update -> run -> (LLM) -> notify.
ENTRYPOINT ["python", "scripts/daily_job.py"]
