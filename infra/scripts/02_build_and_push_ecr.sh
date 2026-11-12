#!/usr/bin/env bash
# =============================================================================
# Step 2: Build & Push Docker Image to Amazon ECR
# =============================================================================
set -euo pipefail
export MSYS_NO_PATHCONV=1

REPO_NAME="${ECR_REPOSITORY:-incident-swarm-api}"
REGION="${AWS_REGION:-$(aws configure get region || echo "ap-south-1")}"

echo "============================================================"
echo "Building & Pushing Docker Image to ECR: ${REPO_NAME} (${REGION})"
echo "============================================================"

ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)
REGISTRY_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_URI="${REGISTRY_URI}/${REPO_NAME}"
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "latest")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# 1. Login to Amazon ECR
echo "🔑 Logging in to Amazon ECR..."
aws ecr get-login-password --region "${REGION}" | docker login --username AWS --password-stdin "${REGISTRY_URI}"

# 2. Build Docker image
echo "🔨 Building Docker image: ${IMAGE_URI}:${GIT_SHA}..."
(cd "${REPO_ROOT}" && docker build --platform linux/amd64 -t "${IMAGE_URI}:${GIT_SHA}" -t "${IMAGE_URI}:latest" .)

# 3. Push to ECR with automatic retries for transient network timeouts
echo "🚀 Pushing image tags to ECR..."
push_with_retries() {
    local target="$1"
    local max_attempts=5
    local attempt=1
    until docker push "${target}"; do
        if [ ${attempt} -ge ${max_attempts} ]; then
            echo "❌ Failed to push ${target} after ${max_attempts} attempts."
            return 1
        fi
        echo "⚠️ Push interrupted or timed out. Resuming in 3 seconds (attempt $((attempt+1))/${max_attempts})..."
        sleep 3
        attempt=$((attempt+1))
    done
}

push_with_retries "${IMAGE_URI}:${GIT_SHA}"
push_with_retries "${IMAGE_URI}:latest"

echo ""
echo "✅  Image successfully pushed to ECR!"
echo "    - Tag: ${IMAGE_URI}:${GIT_SHA}"
echo "    - Tag: ${IMAGE_URI}:latest"
echo "Next step: Run make aws-iam-setup (or ./infra/scripts/03_setup_iam_roles.sh)"

