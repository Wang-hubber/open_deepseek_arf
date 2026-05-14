#!/bin/bash
# Push ARF Docker image to a container registry.
#
# Usage:
#   ./scripts/docker-push.sh <registry> [tag]
#
#   registry: ghcr.io/username  |  docker.io/username  |  registry.example.com/ns
#   tag:      latest (default)  |  v1.0.0  |  commit-sha
#
# Environment variables:
#   DOCKER_USERNAME  — registry username
#   DOCKER_PASSWORD  — registry password / token
#
# Examples:
#   # Push to GitHub Container Registry
#   DOCKER_USERNAME=me DOCKER_PASSWORD=$GH_TOKEN ./scripts/docker-push.sh ghcr.io/me
#
#   # Push to Docker Hub with version tag
#   DOCKER_USERNAME=me DOCKER_PASSWORD=$DH_TOKEN ./scripts/docker-push.sh docker.io/me v1.0.0
#
#   # Push to Alibaba Cloud ACR (国内镜像仓库)
#   DOCKER_USERNAME=your-aliyun-uid@cr DOCKER_PASSWORD=xxx ./scripts/docker-push.sh registry.cn-hangzhou.aliyuncs.com/your-ns

set -euo pipefail

REGISTRY="${1:?Usage: $0 <registry> [tag]}"
TAG="${2:-latest}"
IMAGE="${REGISTRY}/arf:${TAG}"

if [ -z "${DOCKER_USERNAME:-}" ] || [ -z "${DOCKER_PASSWORD:-}" ]; then
    echo "Error: DOCKER_USERNAME and DOCKER_PASSWORD must be set"
    exit 1
fi

echo "=== Logging in to registry ==="
echo "$DOCKER_PASSWORD" | docker login "${REGISTRY%%/*}" -u "$DOCKER_USERNAME" --password-stdin

echo "=== Building image: $IMAGE ==="
docker build -t "$IMAGE" .

echo "=== Pushing image ==="
docker push "$IMAGE"

# Also tag as latest if pushing a version
if [ "$TAG" != "latest" ]; then
    LATEST_IMAGE="${REGISTRY}/arf:latest"
    docker tag "$IMAGE" "$LATEST_IMAGE"
    docker push "$LATEST_IMAGE"
    echo "=== Also pushed: $LATEST_IMAGE ==="
fi

echo "=== Done ==="
echo "  Image: $IMAGE"
echo "  Pull:  docker pull $IMAGE"
