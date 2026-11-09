#!/usr/bin/env bash
# =============================================================================
# Step 1: Create Amazon ECR Repository for Incident Swarm API
# =============================================================================
set -euo pipefail
export MSYS_NO_PATHCONV=1

REPO_NAME="${ECR_REPOSITORY:-incident-swarm-api}"
REGION="${AWS_REGION:-$(aws configure get region || echo "ap-south-1")}"

echo "============================================================"
echo "Creating Amazon ECR Repository: ${REPO_NAME} in ${REGION}"
echo "============================================================"

ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)

# Check if repository already exists
if aws ecr describe-repositories --repository-names "${REPO_NAME}" --region "${REGION}" >/dev/null 2>&1; then
    echo "✅  ECR repository '${REPO_NAME}' already exists."
else
    echo "Creating repository '${REPO_NAME}'..."
    aws ecr create-repository \
        --repository-name "${REPO_NAME}" \
        --region "${REGION}" \
        --image-scanning-configuration scanOnPush=true \
        --tags Key=Project,Value=EnterpriseITSupportSwarm Key=Environment,Value=Production
    echo "✅  Successfully created repository '${REPO_NAME}'."
fi

REPO_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}"
echo ""
echo "🎯 ECR Repository URI: ${REPO_URI}"
echo "Next step: Run make aws-ecr-push (or ./infra/scripts/02_build_and_push_ecr.sh)"

