.DEFAULT_GOAL := help

PYTHON ?= python3
REF ?= latest-release
IMAGE ?= coder-custom
TAG ?= dev
PLATFORM ?= linux
EMBEDDED_OS_ARCHES ?= target
BUILDER_IMAGE ?= $(if $(CODER_BUILDER_IMAGE),$(CODER_BUILDER_IMAGE),coder-coder-builder:amd64)
SMOKE_IMAGE_REF ?= $(if $(strip $(TAG)),$(IMAGE):$(TAG),$(IMAGE))
SMOKE_PLATFORM ?= linux/amd64
SMOKE_PORT ?=
SMOKE_TIMEOUT ?= 180

BUILD_ARGS = --ref $(REF) --image $(IMAGE) --platform $(PLATFORM) --embedded-os-arches $(EMBEDDED_OS_ARCHES)
ifneq ($(strip $(TAG)),)
BUILD_ARGS += --tag $(TAG)
endif

SMOKE_ARGS = --image-ref $(SMOKE_IMAGE_REF) --platform $(SMOKE_PLATFORM) --timeout $(SMOKE_TIMEOUT)
ifneq ($(strip $(SMOKE_PORT)),)
SMOKE_ARGS += --port $(SMOKE_PORT)
endif

.PHONY: help doctor doctor-direct dry-run build push smoke-run run-local rebuild-builder

help:
	@printf '%s\n' 'Coder Builder commands:'
	@printf '%s\n' '  make doctor'
	@printf '%s\n' '  make dry-run REF=latest-release TAG=test PLATFORM=linux'
	@printf '%s\n' '  make build REF=latest-release TAG=dev PLATFORM=arm'
	@printf '%s\n' '  make push IMAGE=ghcr.io/OWNER/REPO/coder TAG=latest PLATFORM=all'
	@printf '%s\n' '  make smoke-run IMAGE=coder-custom TAG=dev'
	@printf '%s\n' '  make run-local IMAGE=coder-custom TAG=dev'
	@printf '%s\n' '  make rebuild-builder'
	@printf '%s\n' ''
	@printf '%s\n' 'PLATFORM accepts linux, arm, or all. arm means linux/arm64 Docker, not Darwin.'
	@printf '%s\n' 'EMBEDDED_OS_ARCHES accepts target, all, or an upstream OS_ARCHES list.'

doctor:
	@$(PYTHON) scripts/doctor.py --mode wrapper

doctor-direct:
	@$(PYTHON) scripts/doctor.py --mode direct

dry-run:
	@$(PYTHON) scripts/build-coder-in-docker.py --dry-run $(BUILD_ARGS)

build:
	@$(PYTHON) scripts/build-coder-in-docker.py $(BUILD_ARGS)

push:
	@$(PYTHON) scripts/build-coder-in-docker.py $(BUILD_ARGS) --push

smoke-run:
	@$(PYTHON) scripts/smoke-run-coder.py $(SMOKE_ARGS)

run-local:
	@$(PYTHON) scripts/smoke-run-coder.py $(SMOKE_ARGS) --keep-running

rebuild-builder:
	@docker build --platform linux/amd64 -f Dockerfile -t $(BUILDER_IMAGE) .
