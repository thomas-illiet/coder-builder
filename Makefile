.DEFAULT_GOAL := help

PYTHON ?= python3
REF ?= latest-release
IMAGE ?= coder-custom
TAG ?= dev
PLATFORM ?= linux
BUILDER_IMAGE ?= $(if $(CODER_BUILDER_IMAGE),$(CODER_BUILDER_IMAGE),coder-coder-builder:amd64)

BUILD_ARGS = --ref $(REF) --image $(IMAGE) --platform $(PLATFORM)
ifneq ($(strip $(TAG)),)
BUILD_ARGS += --tag $(TAG)
endif

.PHONY: help doctor doctor-direct dry-run build push rebuild-builder

help:
	@printf '%s\n' 'Coder Builder commands:'
	@printf '%s\n' '  make doctor'
	@printf '%s\n' '  make dry-run REF=latest-release TAG=test PLATFORM=linux'
	@printf '%s\n' '  make build REF=latest-release TAG=dev PLATFORM=arm'
	@printf '%s\n' '  make push IMAGE=ghcr.io/OWNER/REPO/coder TAG=latest PLATFORM=all'
	@printf '%s\n' '  make rebuild-builder'
	@printf '%s\n' ''
	@printf '%s\n' 'PLATFORM accepts linux, arm, or all.'

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

rebuild-builder:
	@docker build --platform linux/amd64 -f Dockerfile -t $(BUILDER_IMAGE) .
