.DEFAULT_GOAL := help

.PHONY: help check-bootstrap

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "; print "Targets:"} /^[a-zA-Z_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

check-bootstrap: ## Verify the bootstrap commit allowlist
	@set -eu; \
	for file in \
		README.md \
		AGENTS.md \
		.gitignore \
		.editorconfig \
		pyproject.toml \
		Makefile \
		docs/implementation-plan.md \
		docs/progress.md \
		docs/work-history/README.md \
		docs/decisions/README.md \
		terraform.tfvars.example; do \
		test -f "$$file" || { echo "Missing bootstrap file: $$file"; exit 1; }; \
	done; \
	grep -Fqx 'ai_analytics_poc_requirements_aws_v5.md' .gitignore || { echo "Local requirements source must be explicitly ignored"; exit 1; }; \
	if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
		git check-ignore -q -- ai_analytics_poc_requirements_aws_v5.md || { echo "Local requirements source is not ignored by Git"; exit 1; }; \
		if git ls-files --error-unmatch -- ai_analytics_poc_requirements_aws_v5.md >/dev/null 2>&1; then \
			echo "Local requirements source must not be tracked or staged"; \
			exit 1; \
		fi; \
	fi; \
	find . -path './.git' -prune -o -type f -print | sed 's|^\./||' | while IFS= read -r file; do \
		case "$$file" in \
			README.md|AGENTS.md|.gitignore|.editorconfig|pyproject.toml|Makefile|docs/implementation-plan.md|docs/progress.md|docs/work-history/README.md|docs/decisions/README.md|terraform.tfvars.example|ai_analytics_poc_requirements_aws_v5.md) ;; \
			*) if command -v git >/dev/null 2>&1 && git check-ignore -q -- "$$file" 2>/dev/null; then :; else echo "Unexpected non-ignored bootstrap artifact: $$file"; exit 1; fi ;; \
		esac; \
	done
