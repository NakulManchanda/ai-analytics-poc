import json
import os
import sys


def main() -> None:
    if os.getenv("RUN_BEDROCK_SMOKE") != "1":
        sys.exit("Set RUN_BEDROCK_SMOKE=1 to permit the paid Bedrock smoke call.")

    from app.bedrock_smoke import validate_bedrock_smoke_payload
    from app.main import create_app
    from fastapi.testclient import TestClient

    response = TestClient(create_app()).post(
        "/api/ask",
        json={"prompt": "Reply with exactly BEDROCK_SMOKE_OK."},
    )
    response.raise_for_status()

    try:
        payload = validate_bedrock_smoke_payload(response.json())
    except ValueError as error:
        sys.exit(f"Bedrock smoke response did not satisfy its contract: {error}")

    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
