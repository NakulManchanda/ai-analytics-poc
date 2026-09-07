.DEFAULT_GOAL := help

TF_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
TFVARS ?= terraform.tfvars

.PHONY: help init-backendless fmt fmt-check validate plan

help:
	@printf '%s\n' 'Terraform foundation targets:' \
	  '  make init-backendless  Initialize providers without configuring remote state' \
	  '  make fmt              Format Terraform files' \
	  '  make fmt-check        Verify Terraform formatting' \
	  '  make validate         Validate Terraform configuration (after init-backendless)' \
	  '  make plan             Create a read-only plan using TFVARS=$(TFVARS)' \
	  '  make runtime-plan     Save the configured park/resume plan to runtime.tfplan' \
	  '  make runtime-apply    Apply the reviewed runtime.tfplan (authorization required)'

init-backendless:
	terraform -chdir=$(TF_DIR) init -backend=false

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
