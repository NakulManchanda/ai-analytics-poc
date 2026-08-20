.DEFAULT_GOAL := help

APP_HOST_PORT := $(or $(APP_PORT),$(PORT),8080)
MCP_HOST_PORT := $(or $(MCP_PORT),$(PORT),8001)

.PHONY: help check-bootstrap dev mcp-dev mcp-smoke dataset-test dataset-smoke smoke test mcp-test infra-test web-test compose-smoke bedrock-smoke m5-bedrock-smoke m6-bedrock-smoke

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "; print "Targets:"} /^[a-zA-Z_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

check-bootstrap: ## Verify the tracked canonical requirements source
	@set -eu; \
	test -f ai_analytics_poc_requirements_aws_v5.md || { echo "Missing canonical requirements source"; exit 1; }; \
	if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
		git ls-files --error-unmatch -- ai_analytics_poc_requirements_aws_v5.md >/dev/null \
			|| { echo "Canonical requirements source must be tracked"; exit 1; }; \
	fi

dev: ## Run the AI application locally on port 8080
	uv run --project services/app uvicorn app.main:app --host 0.0.0.0 --port $(APP_HOST_PORT) --reload

mcp-dev: ## Run the MCP service locally on port 8001
	uv run --project services/mcp fastmcp run services/mcp/mcp_server/server.py --transport http --host 0.0.0.0 --port $(MCP_HOST_PORT)

mcp-smoke: ## Check MCP dataset capability discovery
	MCP_PORT=$(or $(MCP_PORT),$(PORT)) ./scripts/smoke/01_mcp_empty.sh

dataset-test: ## Run fixture-backed dataset spike tests
	uv run --project services/dataset_spike pytest services/dataset_spike/tests

dataset-smoke: ## Download/reuse pinned NYC TLC files and run the bounded DuckDB profile
	./scripts/smoke/02_dataset_profile.sh

smoke: ## Run all smoke checks for the current milestone
	@if [ -n "$(PORT)" ] && [ -z "$(APP_PORT)" ] && [ -z "$(MCP_PORT)" ]; then \
		echo "Ambiguous PORT for aggregate smoke; set APP_PORT and MCP_PORT explicitly." >&2; exit 2; \
	fi
	AI_APP_URL=http://localhost:$(or $(APP_PORT),8080) ./scripts/smoke/00_health.sh
	MCP_PORT=$(or $(MCP_PORT),8001) ./scripts/smoke/01_mcp_empty.sh
	./scripts/smoke/02_dataset_profile.sh

infra-test: ## Run Terraform static block assertions
	uv run --project services/app pytest tests

web-test: ## Run React tests and production build
	npm --prefix web test
	npm --prefix web run build

compose-smoke: ## Run the browser to FastAPI to FastMCP Compose smoke
	WEB_PORT=$(or $(WEB_PORT),$(PORT)) ./scripts/smoke/03_compose_ui.sh

bedrock-smoke: ## Make one opt-in, bounded paid Bedrock call through POST /api/ask
	uv run --project services/app python services/app/scripts/bedrock_smoke.py

test: ## Run application, MCP, dataset spike, infra, and React checks
	uv run --project services/app pytest services/app/tests
	uv run --project services/mcp pytest services/mcp/tests
	uv run --project services/dataset_spike pytest services/dataset_spike/tests
	uv run --project services/app pytest tests
	$(MAKE) web-test

mcp-test: ## Run MCP tests
	uv run --project services/mcp pytest services/mcp/tests

m5-bedrock-smoke: ## Run the opt-in, bounded two-call Bedrock plus MCP profile smoke
	MCP_PORT=$(or $(MCP_PORT),$(PORT)) ./scripts/smoke/04_m5_bedrock.sh

m6-bedrock-smoke: ## Run the opt-in, bounded two-call Bedrock plus MCP query smoke
	MCP_PORT=$(or $(MCP_PORT),$(PORT)) ./scripts/smoke/04_m6_bedrock.sh

integration-smoke: ## Run end-to-end multi-service integration smoke across all 5 containers
	./scripts/smoke/12_local_integration.sh
