FROM node:22-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
	apt-get install -y --no-install-recommends \
		bash \
		build-essential \
		ca-certificates \
		curl \
		docker.io \
		file \
		git \
		jq \
		libprotobuf-dev \
		make \
		openssh-client \
		protobuf-compiler \
		python3 \
		tar \
		xz-utils \
		zstd && \
	rm -rf /var/lib/apt/lists/* && \
	corepack enable

WORKDIR /workspace
