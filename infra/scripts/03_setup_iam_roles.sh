#!/usr/bin/env bash
# =============================================================================
# Step 3: Setup IAM Execution and Task Roles for ECS Fargate
# =============================================================================
set -euo pipefail

REGION="${AWS_REGION:-$(aws configure get region || echo "ap-south-1")}"
EXEC_ROLE_NAME="ecsTaskExecutionRole"
TASK_ROLE_NAME="ecsTaskRole"

echo "============================================================"
echo "Configuring IAM Roles for ECS Fargate (${REGION})"
echo "============================================================"

ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)

TRUST_POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}'

# 1. Create ecsTaskExecutionRole if it doesn't exist
if aws iam get-role --role-name "${EXEC_ROLE_NAME}" >/dev/null 2>&1; then
    echo "✅  Role '${EXEC_ROLE_NAME}' already exists."
else
    echo "Creating role '${EXEC_ROLE_NAME}'..."
    aws iam create-role \
        --role-name "${EXEC_ROLE_NAME}" \
        --assume-role-policy-document "${TRUST_POLICY}" \
        --description "Execution role for ECS Fargate tasks in Incident Swarm"
    echo "✅  Created role '${EXEC_ROLE_NAME}'."
fi

# Attach managed policy for ECS execution
aws iam attach-role-policy \
    --role-name "${EXEC_ROLE_NAME}" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"

# Attach inline policy to allow pulling secrets from SSM Parameter Store
SSM_SECRET_POLICY="{
  \"Version\": \"2012-10-17\",
  \"Statement\": [
    {
      \"Effect\": \"Allow\",
      \"Action\": [
        \"ssm:GetParameters\",
        \"ssm:GetParameter\"
      ],
      \"Resource\": \"arn:aws:ssm:${REGION}:${ACCOUNT_ID}:parameter/incident-swarm/*\"
    },
    {
      \"Effect\": \"Allow\",
      \"Action\": [
        \"kms:Decrypt\"
      ],
      \"Resource\": \"*\"
    }
  ]
}"

echo "Attaching SSM Parameter Store access policy to '${EXEC_ROLE_NAME}'..."
aws iam put-role-policy \
    --role-name "${EXEC_ROLE_NAME}" \
    --policy-name "ECSTaskExecutionSSMSecretsPolicy" \
    --policy-document "${SSM_SECRET_POLICY}"

# 2. Create ecsTaskRole (used by the running container)
if aws iam get-role --role-name "${TASK_ROLE_NAME}" >/dev/null 2>&1; then
    echo "✅  Role '${TASK_ROLE_NAME}' already exists."
else
    echo "Creating role '${TASK_ROLE_NAME}'..."
    aws iam create-role \
        --role-name "${TASK_ROLE_NAME}" \
        --assume-role-policy-document "${TRUST_POLICY}" \
        --description "Task role for Incident Swarm API container"
    echo "✅  Created role '${TASK_ROLE_NAME}'."
fi

echo ""
echo "✅  IAM roles configured successfully!"
echo "    - Execution Role: arn:aws:iam::${ACCOUNT_ID}:role/${EXEC_ROLE_NAME}"
echo "    - Task Role: arn:aws:iam::${ACCOUNT_ID}:role/${TASK_ROLE_NAME}"
echo "Next step: Run make aws-ecs-deploy (or bash infra/scripts/04_deploy_ecs_fargate.sh)"

