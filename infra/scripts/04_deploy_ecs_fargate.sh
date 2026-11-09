#!/usr/bin/env bash
# =============================================================================
# Step 4: Deploy ECS Fargate Service & Application Load Balancer (ALB)
# =============================================================================
set -euo pipefail
export MSYS_NO_PATHCONV=1

REGION="${AWS_REGION:-$(aws configure get region || echo "ap-south-1")}"
CLUSTER_NAME="${ECS_CLUSTER:-incident-swarm-cluster}"
SERVICE_NAME="${ECS_SERVICE:-incident-swarm-api-service}"
ALB_NAME="incident-swarm-alb"
TG_NAME="incident-swarm-tg"
LOG_GROUP="/ecs/incident-swarm-api"

echo "============================================================"
echo "Deploying ECS Fargate Service & ALB in ${REGION}"
echo "============================================================"

ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/incident-swarm-api:latest"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# 1. Ensure CloudWatch Log Group exists
echo "📝 Ensuring CloudWatch log group exists: ${LOG_GROUP}..."
if ! aws logs describe-log-groups --log-group-name-prefix "${LOG_GROUP}" --region "${REGION}" | grep -q "${LOG_GROUP}"; then
    aws logs create-log-group --log-group-name "${LOG_GROUP}" --region "${REGION}"
    aws logs put-retention-policy --log-group-name "${LOG_GROUP}" --retention-in-days 14 --region "${REGION}"
    echo "✅  Created log group ${LOG_GROUP}."
else
    echo "✅  Log group ${LOG_GROUP} already exists."
fi

# 2. Get Default VPC & Subnets
echo "🌐 Discovering Default VPC and Subnets..."
VPC_ID=$(aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --region "${REGION}" --query "Vpcs[0].VpcId" --output text)
if [ "${VPC_ID}" == "None" ] || [ -z "${VPC_ID}" ]; then
    VPC_ID=$(aws ec2 describe-vpcs --region "${REGION}" --query "Vpcs[0].VpcId" --output text)
fi
echo "Using VPC: ${VPC_ID}"

SUBNET_IDS=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=${VPC_ID}" --region "${REGION}" --query "Subnets[].SubnetId" --output text)
SUBNET_ARRAY=(${SUBNET_IDS})
SUBNET_CSV=$(IFS=, ; echo "${SUBNET_ARRAY[*]}")
echo "Using Subnets: ${SUBNET_CSV}"

# 3. Create Security Groups (ALB SG and ECS Task SG)
echo "🔒 Configuring Security Groups..."
ALB_SG_ID=$(aws ec2 describe-security-groups --filters "Name=vpc-id,Values=${VPC_ID}" "Name=group-name,Values=incident-swarm-alb-sg" --region "${REGION}" --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || echo "None")
if [ "${ALB_SG_ID}" == "None" ] || [ -z "${ALB_SG_ID}" ]; then
    ALB_SG_ID=$(aws ec2 create-security-group --group-name "incident-swarm-alb-sg" --description "Security group for Incident Swarm ALB" --vpc-id "${VPC_ID}" --region "${REGION}" --output text --query "GroupId")
    aws ec2 authorize-security-group-ingress --group-id "${ALB_SG_ID}" --protocol tcp --port 80 --cidr 0.0.0.0/0 --region "${REGION}"
    echo "✅  Created ALB Security Group: ${ALB_SG_ID}"
else
    echo "✅  Using existing ALB Security Group: ${ALB_SG_ID}"
fi

TASK_SG_ID=$(aws ec2 describe-security-groups --filters "Name=vpc-id,Values=${VPC_ID}" "Name=group-name,Values=incident-swarm-task-sg" --region "${REGION}" --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || echo "None")
if [ "${TASK_SG_ID}" == "None" ] || [ -z "${TASK_SG_ID}" ]; then
    TASK_SG_ID=$(aws ec2 create-security-group --group-name "incident-swarm-task-sg" --description "Security group for Incident Swarm ECS Tasks" --vpc-id "${VPC_ID}" --region "${REGION}" --output text --query "GroupId")
    aws ec2 authorize-security-group-ingress --group-id "${TASK_SG_ID}" --protocol tcp --port 8000 --source-group "${ALB_SG_ID}" --region "${REGION}"
    echo "✅  Created Task Security Group: ${TASK_SG_ID}"
else
    echo "✅  Using existing Task Security Group: ${TASK_SG_ID}"
fi

# 4. Create Application Load Balancer
echo "⚖️  Configuring Application Load Balancer..."
ALB_ARN=$(aws elbv2 describe-load-balancers --names "${ALB_NAME}" --region "${REGION}" --query "LoadBalancers[0].LoadBalancerArn" --output text 2>/dev/null || echo "None")
if [ "${ALB_ARN}" == "None" ] || [ -z "${ALB_ARN}" ]; then
    ALB_ARN=$(aws elbv2 create-load-balancer \
        --name "${ALB_NAME}" \
        --subnets ${SUBNET_ARRAY[@]} \
        --security-groups "${ALB_SG_ID}" \
        --scheme internet-facing \
        --type application \
        --region "${REGION}" \
        --query "LoadBalancers[0].LoadBalancerArn" --output text)
    echo "✅  Created ALB: ${ALB_NAME}"
else
    echo "✅  Using existing ALB: ${ALB_NAME}"
fi

ALB_DNS=$(aws elbv2 describe-load-balancers --load-balancer-arns "${ALB_ARN}" --region "${REGION}" --query "LoadBalancers[0].DNSName" --output text)

# 5. Create Target Group
TG_ARN=$(aws elbv2 describe-target-groups --names "${TG_NAME}" --region "${REGION}" --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || echo "None")
if [ "${TG_ARN}" == "None" ] || [ -z "${TG_ARN}" ]; then
    TG_ARN=$(aws elbv2 create-target-group \
        --name "${TG_NAME}" \
        --protocol HTTP \
        --port 8000 \
        --target-type ip \
        --vpc-id "${VPC_ID}" \
        --health-check-path "/health" \
        --health-check-interval-seconds 15 \
        --health-check-timeout-seconds 5 \
        --healthy-threshold-count 2 \
        --unhealthy-threshold-count 3 \
        --region "${REGION}" \
        --query "TargetGroups[0].TargetGroupArn" --output text)
    echo "✅  Created Target Group: ${TG_NAME}"
    
    # Create Listener forwarding to Target Group
    aws elbv2 create-listener \
        --load-balancer-arn "${ALB_ARN}" \
        --protocol HTTP \
        --port 80 \
        --default-actions Type=forward,TargetGroupArn="${TG_ARN}" \
        --region "${REGION}" >/dev/null
    echo "✅  Attached HTTP listener on port 80."
else
    echo "✅  Using existing Target Group: ${TG_NAME}"
fi

# 6. Create ECS Cluster
echo "🏗️  Ensuring ECS Cluster exists: ${CLUSTER_NAME}..."
aws ecs create-cluster --cluster-name "${CLUSTER_NAME}" --region "${REGION}" >/dev/null
echo "✅  ECS Cluster '${CLUSTER_NAME}' ready."

# 7. Render and Register Task Definition
echo "📦 Registering ECS Task Definition..."
RENDERED_TASK_DEF=$(sed \
    -e "s|\${ECR_REPOSITORY_URI}:latest|${ECR_URI}|g" \
    -e "s|\${AWS_ACCOUNT_ID}|${ACCOUNT_ID}|g" \
    -e "s|\${AWS_REGION}|${REGION}|g" \
    "${REPO_ROOT}/infra/ecs-task-definition.json")

TASK_DEF_ARN=$(aws ecs register-task-definition \
    --cli-input-json "${RENDERED_TASK_DEF}" \
    --region "${REGION}" \
    --query "taskDefinition.taskDefinitionArn" --output text)
echo "✅  Registered Task Definition: ${TASK_DEF_ARN}"

# 8. Create or Update ECS Service
echo "🚀 Deploying ECS Fargate Service: ${SERVICE_NAME}..."
if aws ecs describe-services --cluster "${CLUSTER_NAME}" --services "${SERVICE_NAME}" --region "${REGION}" | grep -q "ACTIVE"; then
    echo "Updating existing service..."
    aws ecs update-service \
        --cluster "${CLUSTER_NAME}" \
        --service "${SERVICE_NAME}" \
        --task-definition "${TASK_DEF_ARN}" \
        --desired-count 1 \
        --force-new-deployment \
        --region "${REGION}" >/dev/null
    echo "✅  Service updated."
else
    echo "Creating new service..."
    aws ecs create-service \
        --cluster "${CLUSTER_NAME}" \
        --service-name "${SERVICE_NAME}" \
        --task-definition "${TASK_DEF_ARN}" \
        --desired-count 1 \
        --launch-type FARGATE \
        --platform-version LATEST \
        --network-configuration "awsvpcConfiguration={subnets=[${SUBNET_CSV}],securityGroups=[${TASK_SG_ID}],assignPublicIp=ENABLED}" \
        --load-balancers "targetGroupArn=${TG_ARN},containerName=api,containerPort=8000" \
        --region "${REGION}" >/dev/null
    echo "✅  Service created."
fi

echo ""
echo "============================================================"
echo "🎉 DEPLOYMENT LAUNCHED!"
echo "============================================================"
echo "Live ALB URL: http://${ALB_DNS}"
echo "Health Check: http://${ALB_DNS}/health"
echo "API Docs:     http://${ALB_DNS}/docs"
echo ""
echo "Next step: Wait ~60s for containers to boot, then run:"
echo "           make aws-verify (or bash infra/scripts/05_verify_deployment.sh)"

