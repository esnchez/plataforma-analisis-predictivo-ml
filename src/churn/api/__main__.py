"""Start the API with uvicorn. Long-running (Lambda + API Gateway or an ECS service on AWS).
    python -m churn.api            API_HOST (127.0.0.1), API_PORT (8000), API_WORKERS (1)
"""

import os

import uvicorn

from churn.api.metrics import reset_multiprocess_dir

reset_multiprocess_dir()
uvicorn.run("churn.api.app:app", host=os.environ.get("API_HOST", "127.0.0.1"),
            port=int(os.environ.get("API_PORT", "8000")), workers=int(os.environ.get("API_WORKERS", "1")))
