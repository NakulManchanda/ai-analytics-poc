.DEFAULT_GOAL := help

APP_PORT ?= $(or $(PORT),8080)
MCP_PORT ?= $(or $(PORT),8001)
WEB_PORT ?= $(or $(PORT),3000)

.PHONY: help check-bootstrap dev mcp-dev mcp-smoke dataset-test dataset-smoke smoke test mcp-test infra-test web-test compose-smoke m5-bedrock-smoke

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
	uv run --project services/app uvicorn app.main:app --host 0.0.0.0 --port $(APP_PORT) --reload

mcp-dev: ## Run the MCP service locally on port 8001
	uv run --project services/mcp fastmcp run services/mcp/mcp_server/server.py --transport http --host 0.0.0.0 --port $(MCP_PORT)

mcp-smoke: ## Check MCP dataset capability discovery
	MCP_PORT=$(MCP_PORT) ./scripts/smoke/01_mcp_empty.sh

dataset-test: ## Run fixture-backed dataset spike tests
	uv run --project services/dataset_spike pytest services/dataset_spike/tests

dataset-smoke: ## Download/reuse pinned NYC TLC files and run the bounded DuckDB profile
	./scripts/smoke/02_dataset_profile.sh

smoke: ## Run all smoke checks for the current milestone
	AI_APP_URL=http://localhost:$(APP_PORT) ./scripts/smoke/00_health.sh
	MCP_PORT=$(MCP_PORT) ./scripts/smoke/01_mcp_empty.sh
	./scripts/smoke/02_dataset_profile.sh

infra-test: ## Run Terraform static block assertions
	uv run --project services/app pytest tests

web-test: ## Run React tests and production build
	npm --prefix web test
	npm --prefix web run build

compose-smoke: ## Run the browser to FastAPI to FastMCP Compose smoke
	WEB_PORT=$(WEB_PORT) ./scripts/smoke/03_compose_ui.sh

test: ## Run application, MCP, dataset spike, infra, and React checks
	uv run --project services/app pytest services/app/tests
	uv run --project services/mcp pytest services/mcp/tests
	uv run --project services/dataset_spike pytest services/dataset_spike/tests
	uv run --project services/app pytest tests
	$(MAKE) web-test

mcp-test: ## Run MCP tests
	uv run --project services/mcp pytest services/mcp/tests

m5-bedrock-smoke: ## Run the opt-in, bounded two-call Bedrock plus MCP profile smoke
	MCP_PORT=$(MCP_PORT) ./scripts/smoke/04_m5_bedrock.sh
