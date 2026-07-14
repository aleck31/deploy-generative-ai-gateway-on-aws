#!/bin/bash
# Preflight check for LiteLLM Gateway deployment.
# Run standalone: ./preflight-check.sh
# Use --no-docker to skip docker checks (e.g. for undeploy).
set -uo pipefail

CHECK_DOCKER=true
[ "${1:-}" = "--no-docker" ] && CHECK_DOCKER=false

RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'; NC='\033[0m'
FAIL=0
pass() { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; FAIL=1; }

echo "=== Preflight Check ==="

# 1. Required tools
echo "[1/5] Required tools"
tools="aws terraform python3 yq jq"
$CHECK_DOCKER && tools="$tools docker"
for tool in $tools; do
  if command -v "$tool" >/dev/null 2>&1; then
    pass "$tool installed"
  else
    fail "$tool not found"
  fi
done

# 2. Docker daemon
echo "[2/5] Docker daemon"
if ! $CHECK_DOCKER; then
  echo "  (skipped)"
elif docker info >/dev/null 2>&1; then
  pass "docker daemon running"
else
  fail "docker daemon not running"
fi

# 3. AWS credentials
echo "[3/5] AWS credentials"
CALLER=$(aws sts get-caller-identity --query "Arn" --output text 2>/dev/null)
if [ -n "$CALLER" ]; then
  pass "authenticated as $CALLER"
  ACCOUNT=$(aws sts get-caller-identity --query "Account" --output text 2>/dev/null)
  REGION=$(aws configure get region 2>/dev/null || echo "$AWS_DEFAULT_REGION")
  pass "account=$ACCOUNT region=${REGION:-unset}"
else
  fail "AWS credentials invalid or not configured"
fi

# 4. .env config
echo "[4/5] .env configuration"
if [ -f ".env" ]; then
  pass ".env exists"
  source .env 2>/dev/null || true
  [ -n "${TERRAFORM_S3_BUCKET_NAME:-}" ] && pass "TERRAFORM_S3_BUCKET_NAME set" || fail "TERRAFORM_S3_BUCKET_NAME empty (required)"
  [ -n "${LITELLM_VERSION:-}" ] && pass "LITELLM_VERSION=$LITELLM_VERSION" || fail "LITELLM_VERSION empty (required)"
else
  warn ".env not found (will be created from .env.template)"
fi

# 5. Key IAM permissions (dry-run probes)
echo "[5/5] IAM permissions (probe)"
probe() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then pass "$desc"; else warn "$desc — may lack permission"; fi
}
# Networking
probe "ec2:DescribeVpcs" aws ec2 describe-vpcs --max-results 5
probe "ec2:DescribeManagedPrefixLists" aws ec2 describe-managed-prefix-lists --max-results 5
# Container registry (both platforms)
probe "ecr:DescribeRepositories" aws ecr describe-repositories --max-results 1
# Platform-specific compute
if [ "${DEPLOYMENT_PLATFORM:-ECS}" = "EKS" ]; then
  probe "eks:ListClusters" aws eks list-clusters --max-results 1
else
  probe "ecs:ListClusters" aws ecs list-clusters --max-results 1
  probe "application-autoscaling:DescribeScalableTargets" aws application-autoscaling describe-scalable-targets --service-namespace ecs
fi
# Data stores
probe "rds:DescribeDBInstances" aws rds describe-db-instances --max-records 20
probe "elasticache:DescribeReplicationGroups" aws elasticache describe-replication-groups --max-records 20
# Load balancing
probe "elasticloadbalancing:DescribeLoadBalancers" aws elbv2 describe-load-balancers --page-size 1
# Security / identity
probe "iam:ListPolicies" aws iam list-policies --max-items 1
probe "secretsmanager:ListSecrets" aws secretsmanager list-secrets --max-results 1
probe "kms:ListKeys" aws kms list-keys --limit 1
probe "acm:ListCertificates" aws acm list-certificates --max-items 1
# DNS / logs
if [ "${USE_ROUTE53:-false}" = "true" ]; then
  probe "route53:ListHostedZones" aws route53 list-hosted-zones --max-items 1
fi
probe "logs:DescribeLogGroups" aws logs describe-log-groups --limit 1
# Storage
probe "s3:ListAllMyBuckets" aws s3api list-buckets
# WAF (only if enabled)
if [ "${ENABLE_WAF:-true}" = "true" ]; then
  probe "wafv2:ListWebACLs" aws wafv2 list-web-acls --scope REGIONAL
fi
# CloudFront (only if enabled)
if [ "${USE_CLOUDFRONT:-true}" = "true" ]; then
  probe "cloudfront:ListDistributions" aws cloudfront list-distributions --max-items 1
fi
# Service Catalog AppRegistry (solution tagging - common perm gap)
probe "servicecatalog:ListApplications" aws servicecatalog-appregistry list-applications --max-results 1

echo "======================="
if [ "$FAIL" -eq 1 ]; then
  echo -e "${RED}Preflight FAILED — fix the errors above before deploying.${NC}"
  exit 1
else
  echo -e "${GREEN}Preflight passed.${NC}"
fi
