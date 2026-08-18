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
	  '  make plan             Create a read-only plan using TFVARS=$(TFVARS)'

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
