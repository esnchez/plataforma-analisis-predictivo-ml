# One Dockerfile, two images (build targets):
#   api   boto3 + FastAPI only, for the always-on API         (Lambda + API Gateway or ECS service on AWS)
#   jobs  + pandas / XGBoost / scikit-learn, for run-and-exit jobs: training, scoring, monitoring,
#         and the local-only seed and backtest
# The command decides which job a jobs container runs: python -m churn.training | churn.scoring | ...

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml ./
COPY churn ./churn

FROM base AS api
RUN pip install ".[api]"
# PROMETHEUS_MULTIPROC_DIR: the 4 workers share metric files there, so /metrics reports all of them.
ENV API_HOST=0.0.0.0 API_PORT=8000 API_WORKERS=4 PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-metrics
EXPOSE 8000
CMD ["python", "-m", "churn.api"]

FROM base AS jobs
# XGBoost needs the OpenMP runtime, which the slim image does not include.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
RUN pip install ".[jobs]"
CMD ["python", "-m", "churn.scoring"]
