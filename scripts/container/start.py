"""Start uvicorn without requiring a shell in the production image."""
import os

os.execv("/usr/local/bin/python", [
    "python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0",
    "--port", os.environ.get("PORT", "10000"), "--no-proxy-headers",
])
