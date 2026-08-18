import json
import os
import sys

if os.getenv("RUN_BEDROCK_SMOKE") != "1":
    sys.exit("Set RUN_BEDROCK_SMOKE=1 to permit the paid Bedrock smoke call.")

from app.main import create_app
from fastapi.testclient import TestClient

response = TestClient(create_app()).post(
    "/api/ask",
    json={"prompt": "Reply with exactly BEDROCK_SMOKE_OK."},
)
response.raise_for_status()

print(json.dumps(response.json(), sort_keys=True))
