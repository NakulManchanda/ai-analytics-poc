.DEFAULT_GOAL := help

TF_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
TFVARS ?= terraform.tfvars
BACKEND_CONFIG ?= backend.hcl

.PHONY: help init-backendless init-remote fmt fmt-check validate plan dispatch resume park

help:
	@printf '%s\n' 'Terraform foundation targets:' \
	  '  make init-backendless  Initialize providers without configuring remote state' \
	  '  make init-remote       Initialize the approved S3 backend using ignored backend.hcl' \
	  '  make fmt              Format Terraform files' \
	  '  make fmt-check        Verify Terraform formatting' \
	  '  make validate         Validate Terraform configuration (after init-backendless)' \
	  '  make plan             Create a read-only plan using TFVARS=$(TFVARS)' \
	  '  make runtime-plan     Save the configured park/resume plan to runtime.tfplan' \
	  '  make runtime-apply    Apply the reviewed runtime.tfplan (authorization required)' \
	  '  make dispatch         Trigger approved Terraform release on GitHub Actions via gh CLI' \
	  '  make resume           Run Terraform on GitHub Actions with demo_enabled=true' \
	  '  make park             Run Terraform on GitHub Actions with demo_enabled=false'



init-backendless:
	terraform -chdir=$(TF_DIR) init -backend=false

init-remote:
	@test -f $(BACKEND_CONFIG) || { echo "Missing ignored $(BACKEND_CONFIG); copy backend.hcl.example." >&2; exit 2; }
	terraform -chdir=$(TF_DIR) init -reconfigure -backend-config=$(BACKEND_CONFIG)

fmt:
	terraform -chdir=$(TF_DIR) fmt

fmt-check:
	terraform -chdir=$(TF_DIR) fmt -check

validate:
	terraform -chdir=$(TF_DIR) validate

plan:
	terraform -chdir=$(TF_DIR) plan -var-file=$(TFVARS)

# These use the persisted local tfvars. Review runtime-plan before runtime-apply.
.PHONY: runtime-plan runtime-apply
runtime-plan:
	terraform -chdir=$(TF_DIR) plan -var-file=$(TFVARS) -out=runtime.tfplan

runtime-apply:
	terraform -chdir=$(TF_DIR) apply runtime.tfplan

dispatch: ## Run Terraform release on GitHub Actions (usage: make dispatch [REF=main] [DEMO=false])
	@REF=$${REF:-main}; \
	DEMO_VAL=$${DEMO:-false}; \
	gh workflow run terraform-release.yml --ref main -f release_tag="$$REF" -f demo_enabled="$$DEMO_VAL" && \
	gh run watch

resume: ## Run Terraform on GitHub Actions with demo_enabled=true (resumes paid demo backend)
	@$(MAKE) dispatch REF=$(or $(REF),main) DEMO=true

park: ## Run Terraform on GitHub Actions with demo_enabled=false (parks demo backend to stop costs)
	@$(MAKE) dispatch REF=$(or $(REF),main) DEMO=false
