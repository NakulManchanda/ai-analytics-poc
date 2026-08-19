import json
import os
import sys


def main() -> None:
    if os.getenv("RUN_BEDROCK_SMOKE") != "1":
        sys.exit("Set RUN_BEDROCK_SMOKE=1 to permit the paid M5 Bedrock smoke call.")

    from app.m5_bedrock_smoke import validate_m5_bedrock_smoke_payload
    from app.main import create_app
    from fastapi.testclient import TestClient

    response = TestClient(create_app()).post(
        "/api/ask",
        json={
            "prompt": (
                "Use the dataset profile tool, then reply with exactly "
                "M5_BEDROCK_SMOKE_OK."
            )
        },
    )
    response.raise_for_status()

    try:
        payload = validate_m5_bedrock_smoke_payload(response.json())
    except ValueError as error:
        sys.exit(f"M5 Bedrock smoke response did not satisfy its contract: {error}")

    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
