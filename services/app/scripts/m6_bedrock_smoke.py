import json
import os
import sys


def main() -> None:
    if os.getenv("RUN_BEDROCK_SMOKE") != "1":
        sys.exit("Set RUN_BEDROCK_SMOKE=1 to permit the paid M6 Bedrock smoke call.")

    import boto3

    REQUIRED_ACCOUNT_ID = "107207236011"
    sts_client = boto3.client("sts")
    account = sts_client.get_caller_identity().get("Account")
    if account != REQUIRED_ACCOUNT_ID:
        sys.exit(f"Expected AWS account {REQUIRED_ACCOUNT_ID}, got {account}")

    from app.m6_bedrock_smoke import validate_m6_bedrock_smoke_payload
    from app.main import create_app
    from fastapi.testclient import TestClient

    response = TestClient(create_app()).post(
        "/api/ask",
        json={"prompt": "Which pickup zones have the most trips in the NYC taxi data?"},
    )
    response.raise_for_status()

    try:
        payload = validate_m6_bedrock_smoke_payload(response.json())
    except ValueError as error:
        sys.exit(f"M6 Bedrock smoke response did not satisfy its contract: {error}")

    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
