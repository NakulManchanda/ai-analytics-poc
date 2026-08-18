.DEFAULT_GOAL := help

.PHONY: help check-bootstrap dev mcp-dev mcp-smoke smoke test mcp-test

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
	uv run --project services/app uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

mcp-dev: ## Run the empty MCP service locally on port 8001
	uv run --project services/mcp fastmcp run services/mcp/mcp_server/server.py --transport http --host 0.0.0.0 --port 8001

mcp-smoke: ## Check the empty MCP protocol contract
	./scripts/smoke/01_mcp_empty.sh

smoke: ## Run all smoke checks for the current milestone
	./scripts/smoke/00_health.sh
	./scripts/smoke/01_mcp_empty.sh

test: ## Run application and MCP tests
	uv run --project services/app pytest services/app/tests
	uv run --project services/mcp pytest services/mcp/tests

mcp-test: ## Run MCP tests
	uv run --project services/mcp pytest services/mcp/tests
