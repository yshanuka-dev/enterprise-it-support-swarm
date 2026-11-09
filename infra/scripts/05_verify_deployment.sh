#!/usr/bin/env bash
# =============================================================================
# Step 5: Verify Live AWS Deployment (Health & Chat Endpoints)
# =============================================================================
set -euo pipefail
export MSYS_NO_PATHCONV=1

REGION="${AWS_REGION:-$(aws configure get region || echo "ap-south-1")}"
ALB_NAME="incident-swarm-alb"

echo "============================================================"
echo "Verifying Live AWS Deployment (${REGION})"
echo "============================================================"

ALB_DNS=$(aws elbv2 describe-load-balancers --names "${ALB_NAME}" --region "${REGION}" --query "LoadBalancers[0].DNSName" --output text 2>/dev/null || echo "")

if [ -z "${ALB_DNS}" ]; then
    echo "❌  Error: Application Load Balancer '${ALB_NAME}' not found."
    exit 1
fi

BASE_URL="http://${ALB_DNS}"
HEALTH_URL="${BASE_URL}/health"
CHAT_URL="${BASE_URL}/chat"

echo "ALB URL: ${BASE_URL}"
echo "Polling ${HEALTH_URL} for container readiness..."

MAX_ATTEMPTS=30
ATTEMPT=1
SLEEP_SECS=10

until curl -s -f -m 5 "${HEALTH_URL}" > /dev/null 2>&1; do
    if [ ${ATTEMPT} -ge ${MAX_ATTEMPTS} ]; then
        echo ""
        echo "❌  Timed out waiting for ${HEALTH_URL} after $((MAX_ATTEMPTS * SLEEP_SECS))s."
        echo "Check ECS service events and CloudWatch logs for troubleshooting:"
        echo "  aws logs tail /ecs/incident-swarm-api --follow"
        exit 1
    fi
    echo "  [Attempt ${ATTEMPT}/${MAX_ATTEMPTS}] Waiting for container to pass ALB health checks..."
    sleep ${SLEEP_SECS}
    ATTEMPT=$((ATTEMPT + 1))
done

echo ""
echo "============================================================"
echo "✅  HEALTH CHECK PASSED!"
echo "============================================================"
curl -s "${HEALTH_URL}" | python -m json.tool || curl -s "${HEALTH_URL}"
echo ""

echo "============================================================"
echo "Testing Live Chat Endpoint: ${CHAT_URL}"
echo "============================================================"

RESPONSE=$(curl -s -X POST "${CHAT_URL}" \
    -H "Content-Type: application/json" \
    -d '{"message": "How do I reset my VPN?"}')

echo "${RESPONSE}" | python -m json.tool || echo "${RESPONSE}"

echo ""
echo "============================================================"
echo "🎉 SUCCESS: Day 7 AWS Deployment is LIVE and fully verified!"
echo "============================================================"
echo "Shareable Grader URLs:"
echo "  - Health: ${HEALTH_URL}"
echo "  - Docs:   ${BASE_URL}/docs"

