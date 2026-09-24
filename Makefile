.DEFAULT_GOAL := help

APP_HOST_PORT := $(or $(APP_PORT),$(PORT),8080)
MCP_HOST_PORT := $(or $(MCP_PORT),$(PORT),8001)
OBSERVABILITY_PROJECT ?= ai-analytics-128
OBSERVABILITY_WEB_PORT ?= 13000
OBSERVABILITY_JAEGER_PORT ?= 16686
OBSERVABILITY_GRAFANA_PORT ?= 13001
OBSERVABILITY_PROMETHEUS_PORT ?= 19090
OBSERVABILITY_LOG_TAIL ?= 200

.PHONY: help check-bootstrap dev mcp-dev mcp-smoke dataset-test dataset-smoke smoke test mcp-test infra-test web-test compose-smoke observability-up observability-down observability-smoke observability-dev-up observability-dev-info observability-dev-ask observability-dev-metrics observability-dev-logs observability-dev-down local-aws-compose local-aws-refresh local-bedrock-compose bedrock-smoke m5-bedrock-smoke m6-bedrock-smoke dashboard tf-dispatch tf-resume tf-park


help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "; print "Targets:"} /^[a-zA-Z_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

dashboard: ## Run local Streamlit metrics & model comparison dashboard
	uv run --with streamlit --with duckdb streamlit run scripts/dashboard.py


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

observability-up: ## Start local Compose with OTEL Collector, Jaeger, Grafana, and Prometheus
	WEB_PORT=$(or $(WEB_PORT),3000) JAEGER_UI_PORT=$(or $(JAEGER_UI_PORT),16686) \
	GRAFANA_PORT=$(or $(GRAFANA_PORT),13001) PROMETHEUS_PORT=$(or $(PROMETHEUS_PORT),19090) \
		docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d

observability-down: ## Stop the local observability Compose stack
	docker compose -f docker-compose.yml -f docker-compose.observability.yml down

observability-smoke: ## Verify an application ai.run trace reaches Jaeger
	./scripts/smoke/16_observability.sh

observability-dev-up: ## Start the issue-owned local app, Collector, Jaeger, Grafana, and Prometheus stack
	WEB_PORT="$(OBSERVABILITY_WEB_PORT)" JAEGER_UI_PORT="$(OBSERVABILITY_JAEGER_PORT)" \
	GRAFANA_PORT="$(OBSERVABILITY_GRAFANA_PORT)" PROMETHEUS_PORT="$(OBSERVABILITY_PROMETHEUS_PORT)" \
		docker compose -p "$(OBSERVABILITY_PROJECT)" -f docker-compose.yml -f docker-compose.observability.yml up --build -d

observability-dev-info: ## Print issue-owned observability URLs, expected spans, and Compose status
	@set -eu; \
	printf '%s\n' "Web:        http://127.0.0.1:$(OBSERVABILITY_WEB_PORT)"; \
	printf '%s\n' "Jaeger:     http://127.0.0.1:$(OBSERVABILITY_JAEGER_PORT)"; \
	printf '%s\n' "Grafana:    http://127.0.0.1:$(OBSERVABILITY_GRAFANA_PORT)"; \
	printf '%s\n' "Prometheus: http://127.0.0.1:$(OBSERVABILITY_PROMETHEUS_PORT)"; \
	printf '%s\n' "Metrics:    metrics/runs.jsonl"; \
	printf '%s\n' "Expected spans: ai.run -> mcp.request -> mcp.tool.execute -> duckdb.query"; \
	printf '%s\n' "Compose project: $(OBSERVABILITY_PROJECT)"; \
	docker compose -p "$(OBSERVABILITY_PROJECT)" -f docker-compose.yml -f docker-compose.observability.yml ps

observability-dev-ask: ## Send a safe representative request to the issue-owned local stack
	curl --fail --show-error --silent \
		-X POST "http://127.0.0.1:$(OBSERVABILITY_WEB_PORT)/api/ask" \
		-H 'content-type: application/json' \
		--data '{"prompt":"Which pickup zones have the most trips?"}'

observability-dev-metrics: ## Print recent local JSONL metrics; app stdout emits EMF and make dashboard compares runs
	@set -eu; \
	if test -f metrics/runs.jsonl; then \
		echo "Recent metrics/runs.jsonl entries:"; \
		tail -n 20 metrics/runs.jsonl; \
	else \
		echo "No local metrics/runs.jsonl yet; submit a request first."; \
	fi; \
	echo "Application stdout emits CloudWatch EMF; run 'make dashboard' to compare local JSONL runs."

observability-dev-logs: ## Print recent app, MCP, and Collector logs; append -f to the shown command to follow
	@docker compose -p "$(OBSERVABILITY_PROJECT)" -f docker-compose.yml -f docker-compose.observability.yml logs --tail "$(OBSERVABILITY_LOG_TAIL)" app mcp otel-collector; \
	echo "Follow live logs with: docker compose -p '$(OBSERVABILITY_PROJECT)' -f docker-compose.yml -f docker-compose.observability.yml logs -f app mcp otel-collector"

observability-dev-down: ## Stop only the issue-owned local observability project and remove its orphans
	WEB_PORT="$(OBSERVABILITY_WEB_PORT)" JAEGER_UI_PORT="$(OBSERVABILITY_JAEGER_PORT)" \
	GRAFANA_PORT="$(OBSERVABILITY_GRAFANA_PORT)" PROMETHEUS_PORT="$(OBSERVABILITY_PROMETHEUS_PORT)" \
		docker compose -p "$(OBSERVABILITY_PROJECT)" -f docker-compose.yml -f docker-compose.observability.yml down --remove-orphans

local-aws-compose: ## Start local Compose with opt-in real AWS (Bedrock + Transcribe) and shared DynamoDB budget
	DYNAMODB_TABLE_NAME=$(or $(DYNAMODB_TABLE_NAME),ai-analytics-poc-demo-application-state) \
	AWS_PROFILE=$(or $(AWS_PROFILE),default) \
	LOCAL_UID=$$(id -u) \
	WEB_PORT=$(or $(WEB_PORT),3000) \
	docker compose -f docker-compose.yml -f docker-compose.aws.yml up --build -d

local-aws-refresh: ## Restart local Compose stack from scratch (down then rebuild/up)
	docker compose -f docker-compose.yml -f docker-compose.aws.yml down
	$(MAKE) local-aws-compose

local-bedrock-compose: local-aws-compose ## Deprecated alias for local-aws-compose

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

ecs-smoke: ## Run smoke check against deployed ECS Application Load Balancer
	./scripts/smoke/14_ecs_backend_smoke.sh

cloudfront-smoke: ## Run end-to-end smoke check against CloudFront distribution
	./scripts/smoke/15_cloudfront_e2e.sh

deploy-frontend: ## Build React app, upload to S3, and invalidate CloudFront cache
	./scripts/deploy_frontend.sh

tf-dispatch: ## Run Terraform release on GitHub Actions (usage: make tf-dispatch [REF=main] [DEMO=false])
	@REF=$${REF:-main}; \
	DEMO_VAL=$${DEMO:-false}; \
	echo "Dispatching Terraform release on GitHub Actions (ref=$$REF, demo_enabled=$$DEMO_VAL)..."; \
	gh workflow run terraform-release.yml --ref main -f release_tag="$$REF" -f demo_enabled="$$DEMO_VAL" && \
	echo "Workflow dispatched. Watching run..." && \
	gh run watch

tf-resume: ## Run Terraform on GitHub Actions with demo_enabled=true (resumes paid demo backend)
	@$(MAKE) tf-dispatch REF=$(or $(REF),main) DEMO=true

tf-park: ## Run Terraform on GitHub Actions with demo_enabled=false (parks demo backend to stop costs)
	@$(MAKE) tf-dispatch REF=$(or $(REF),main) DEMO=false
