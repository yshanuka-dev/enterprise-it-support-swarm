#!/usr/bin/env bash
# =============================================================================
# Step 99: Teardown / Destroy AWS Infrastructure (Cost Prevention)
# =============================================================================
set -euo pipefail
export MSYS_NO_PATHCONV=1

REGION="${AWS_REGION:-$(aws configure get region || echo "ap-south-1")}"
CLUSTER_NAME="${ECS_CLUSTER:-incident-swarm-cluster}"
SERVICE_NAME="${ECS_SERVICE:-incident-swarm-api-service}"
ALB_NAME="incident-swarm-alb"
TG_NAME="incident-swarm-tg"
ALB_SG_NAME="incident-swarm-alb-sg"
TASK_SG_NAME="incident-swarm-task-sg"
LOG_GROUP="/ecs/incident-swarm-api"

echo "============================================================"
echo "🚨 TEARDOWN AWS INFRASTRUCTURE: ${REGION}"
echo "This will destroy the ECS Service, ALB, and Target Group to avoid costs."
echo "============================================================"

# 1. Scale down and delete ECS Service
echo "🛑 Stopping and deleting ECS Service: ${SERVICE_NAME}..."
if aws ecs describe-services --cluster "${CLUSTER_NAME}" --services "${SERVICE_NAME}" --region "${REGION}" | grep -q "ACTIVE"; then
    aws ecs update-service --cluster "${CLUSTER_NAME}" --service "${SERVICE_NAME}" --desired-count 0 --region "${REGION}" >/dev/null 2>&1 || true
    echo "  Scaled desired task count to 0."
    aws ecs delete-service --cluster "${CLUSTER_NAME}" --service "${SERVICE_NAME}" --force --region "${REGION}" >/dev/null
    echo "✅  Deleted ECS Service."
else
    echo "  ECS Service not found or already deleted."
fi

# 2. Delete ECS Cluster
echo "🛑 Deleting ECS Cluster: ${CLUSTER_NAME}..."
aws ecs delete-cluster --cluster "${CLUSTER_NAME}" --region "${REGION}" >/dev/null 2>&1 || true
echo "✅  Deleted ECS Cluster."

# 3. Delete ALB and Listeners
echo "🛑 Finding and deleting Application Load Balancer: ${ALB_NAME}..."
ALB_ARN=$(aws elbv2 describe-load-balancers --names "${ALB_NAME}" --region "${REGION}" --query "LoadBalancers[0].LoadBalancerArn" --output text 2>/dev/null || echo "")
if [ -n "${ALB_ARN}" ] && [ "${ALB_ARN}" != "None" ]; then
    # Delete listeners first
    LISTENER_ARNS=$(aws elbv2 describe-listeners --load-balancer-arn "${ALB_ARN}" --region "${REGION}" --query "Listeners[].ListenerArn" --output text 2>/dev/null || echo "")
    for L_ARN in ${LISTENER_ARNS}; do
        aws elbv2 delete-listener --listener-arn "${L_ARN}" --region "${REGION}" >/dev/null 2>&1 || true
    done
    aws elbv2 delete-load-balancer --load-balancer-arn "${ALB_ARN}" --region "${REGION}" >/dev/null
    echo "✅  Deleted Application Load Balancer."
    echo "  Waiting for ALB network interfaces to release..."
    sleep 15
else
    echo "  ALB not found or already deleted."
fi

# 4. Delete Target Group
echo "🛑 Deleting Target Group: ${TG_NAME}..."
TG_ARN=$(aws elbv2 describe-target-groups --names "${TG_NAME}" --region "${REGION}" --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || echo "")
if [ -n "${TG_ARN}" ] && [ "${TG_ARN}" != "None" ]; then
    aws elbv2 delete-target-group --target-group-arn "${TG_ARN}" --region "${REGION}" >/dev/null 2>&1 || true
    echo "✅  Deleted Target Group."
else
    echo "  Target Group not found or already deleted."
fi

# 5. Delete Security Groups (if not locked by lingering ENIs)
echo "🛑 Cleaning up Security Groups..."
TASK_SG_ID=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=${TASK_SG_NAME}" --region "${REGION}" --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || echo "")
if [ -n "${TASK_SG_ID}" ] && [ "${TASK_SG_ID}" != "None" ]; then
    aws ec2 delete-security-group --group-id "${TASK_SG_ID}" --region "${REGION}" >/dev/null 2>&1 || true
    echo "  Cleaned up Task SG: ${TASK_SG_NAME}"
fi

ALB_SG_ID=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=${ALB_SG_NAME}" --region "${REGION}" --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || echo "")
if [ -n "${ALB_SG_ID}" ] && [ "${ALB_SG_ID}" != "None" ]; then
    aws ec2 delete-security-group --group-id "${ALB_SG_ID}" --region "${REGION}" >/dev/null 2>&1 || true
    echo "  Cleaned up ALB SG: ${ALB_SG_NAME}"
fi

echo ""
echo "============================================================"
echo "🎉 TEARDOWN COMPLETE! No active ECS/ALB charges will occur."
echo "Note:"
echo " - Your ECR image and SSM secrets are preserved for future deploys."
echo " - To re-deploy anytime, run: make aws-ecs-deploy"
echo "============================================================"

