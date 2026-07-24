# Deploy Generative AI Gateway on AWS

> Fork 自 [aws-solutions-library-samples](https://github.com/aws-solutions-library-samples/guidance-for-multi-provider-generative-ai-gateway-on-aws)，针对生产环境做了修复和增强。

## Changelog

### v1.2.0
- 新增 **Lark（飞书）告警**支持：只需在 `.env` 设置 `LARK_WEBHOOK_URL`（可选 `LARK_WEBHOOK_SECRET` 签名校验）即可，`deploy.sh` 自动接线
  - middleware 新增内部端点 `/webhook/slack-to-lark`（独立模块 `middleware/lark_alerting.py`），把 LiteLLM 的 Slack 格式告警翻译成 Lark 卡片转发,覆盖全部告警类型
  - LiteLLM 无原生 Lark 通道,此处借用其 Slack 通道 + middleware 桥接实现（原理同官方 Discord/Teams 集成）
- 新增原生 **Slack 告警**支持：设置 `SLACK_WEBHOOK_URL` 即可
- Slack 与 Lark **可独立启用、互不干扰**；两者都配置时告警同时发往二者（`alert_to_webhook_url` fan-out）
- `deploy.sh` 现在**总是重新生成** `config/config.yaml`，避免遗留的旧配置导致部署未生效

### v1.1.0
- **路由重构**：middleware 增值功能统一迁移到 `/plus/*` 路径前缀，与 LiteLLM 原生端点彻底分离
  - `https://<host>`（无论带不带 `/v1`）→ LiteLLM 原生，标准 OpenAI 兼容行为，流式含 SSE 终止标记 `data: [DONE]`
  - `https://<host>/plus`（无论带不带 `/v1`）→ middleware，承载聊天历史（`session_id`）、Bedrock Managed Prompt、Bedrock 原生接口等增值功能
- 两个 base_url 前缀完全区分，标准 OpenAI 客户端无需纠结是否带 `/v1`
- ECS（ALB 规则合并为单条 `/plus/*`）与 EKS（ingress 合并为单个 `/plus`）路由同步调整
- **破坏性变更**：所有 middleware 功能（`session_id` 聊天历史、Bedrock Prompt、`/bedrock/model/*` 接口、`/chat-history`、`/session-ids` 等）的访问路径需加 `/plus` 前缀

### v1.0.3
- 升级默认 LiteLLM 版本至 v1.91.3
- 扩充默认模型列表（Claude Opus 4.8 / Sonnet 5 / Fable 5、Grok 4.3、MiniMax M2.5、Qwen3-VL、Gemma 4）
- 新增 `scripts/preflight-check.sh` 部署前自检（工具依赖、AWS 凭证、IAM 权限），deploy/undeploy 自动调用

### v1.0.2
- 升级默认 LiteLLM 版本至 v1.88.1
- 添加 `bedrock-mantle:*` IAM 权限，支持 Bedrock Mantle 端点调用
- models.yaml 同时支持简写和完整两种配置格式
- 新增 `BEDROCK_MANTLE_API_KEY` 环境变量，自动注入到模型配置中

### v1.0.1
- 添加 `ENABLE_WAF` 开关（默认 true），可通过 .env 控制是否部署 WAF
- WAF 规则组设为 count 模式（只记录不阻断），避免误拦 LLM 请求
- 添加 `aws-marketplace:Subscribe` 权限，解决第三方模型首次调用报错
- 部署前自动检查版本更新并提示

### v1.0.0
- 修复 middleware `requests` 依赖缺失（容器崩溃循环重启）
- 简化模型配置：`config/models.yaml` 替代 17 个 per-region yaml 文件
- 新增 `BEDROCK_INFERENCE_REGION`，支持跨区域调用 Bedrock
- 新增 `VPC_CIDR_BLOCK`、`ECS_MEMORY_GB`、`ALB_ALLOWED_PREFIX_LIST_ID` 参数化配置
- ALB 安全组支持 Prefix List 白名单（自动创建或用户指定）和 CloudFront origin prefix list
- ALB idle timeout 调至 3000s，支持长任务
- 日志级别从 DEBUG 改为 INFO
- 部署完自动输出 Master Key、URL、Prefix List ID
- 时区自动检测

## Table of contents

- [Project Overview](#project-overview)
- [Architecture](#architecture)
- [How to Deploy](#how-to-deploy)
- [API Endpoints](#api-endpoints)
- [Distribution Options](#distribution-options)
- [AWS Services in this Guidance](#aws-services-in-this-Guidance)
- [Cost](#cost)
   - [Cost Considerations](#cost-considerations)
   - [Cost Components](#cost-components)
   - [Key Factors Influencing AWS Infrastructure Costs](#key-factors-influencing-aws-infrastructure-costs)
   - [Sample Cost Tables](#sample-cost-tables)
- [Security](#security)
- [Open Source Library](#open-source-library)
- [Notices](#notices)

## Project Overview

This project provides a simple Terraform deployment of [LiteLLM](https://github.com/BerriAI/litellm) into Amazon Elastic Container Service (ECS) and Elastic Kubernetes Service (EKS) platforms on AWS. It aims to be pre-configured with defaults that will allow most users to quickly get started with LiteLLM.

It also provides additional features on top of LiteLLM such as an AWS Bedrock Interface (instead of the default OpenAI interface), support for AWS Bedrock Managed Prompts, Chat History, and support for Okta Oauth 2.0 JWT Token Auth.

If you are unfamiliar with LiteLLM, it provides a consistent interface to access all LLM providers so you do not need to edit your code to try out different models. It allows you to centrally manage and track LLM usage across your company at the user, team, and api key level. You can configure budgets and rate limits, restrict access to specific models, and set up retry/fallback routing logic across multiple providers. It provides cost saving measures like prompt caching. It provides security features like support for AWS Bedrock Guardrails for all LLM providers. Finally, it provides a UI where administrators can configure their users and teams, and users can generate their api keys and test out different LLMs in a chat interface.

## Architecture

![Reference Architecture Diagram ECS EKS](./media/Gateway-Architecture-with-CloudFront.png)

### Architecture steps

1. Tenants and client applications access the LiteLLM gateway proxy API through the [Amazon Route 53](https://aws.amazon.com/route53/) URL endpoint or [Amazon CloudFront](https://aws.amazon.com/cloudfront/) distribution, which is protected against common web exploits and bots using [AWS Web Application Firewall (WAF)](https://aws.amazon.com/waf/).
2. AWS WAF forwards requests to [Application Load Balancer (ALB)](https://aws.amazon.com/elasticloadbalancing/application-load-balancer/)to automatically distribute incoming application traffic to [Amazon Elastic Container Service (ECS)](https://aws.amazon.com/ecs/) tasks or [Amazon Elastic Kubernetes Service (EKS)](https://aws.amazon.com/eks/) pods running generative AI gateway containers. TLS/SSL encryption secures traffic using a certificate issued by [AWS Certificate Manager (ACM)](https://aws.amazon.com/certificate-manager/).
3. Container images for API/middleware and LiteLLM applications are built during guidance deployment and pushed to [Amazon Elastic Container registry (ECR)](http://aws.amazon.com/ecr/). They are used for deployment to Amazon ECS on AWS Fargate or Amazon EKS clusters that run these applications as containers in ECS tasks or EKS pods, respectively. LiteLLM provides a unified application interface for configuration and interacting with LLM providers. The API/middleware integrates natively with [Amazon Bedrock](https://aws.amazon.com/bedrock/) to enable features not supported by the [LiteLLM Open source project](https://docs.litellm.ai/).
4. Models hosted on [Amazon Bedrock](https://aws.amazon.com/bedrock/) and [Amazon Nova](https://aws.amazon.com/ai/generative-ai/nova/) provide model access, guardrails, prompt caching, and routing to enhance the AI gateway and additional controls for clients through a unified API. Model access is also available for models deployed on [Amazon SageMaker AI](https://aws.amazon.com/sagemaker-ai/). [Access to required Amazon Bedrock models](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access-modify.html) must be properly configured. 
5. External model providers (such as OpenAI, Anthropic, or Vertex AI) are configured using the LiteLLM Admin UI to enable additional model access through LiteLLM’s unified application interface. Integrate pre-existing configurations of third-party providers into the gateway using LiteLLM APIs. 
6. LiteLLM integrates with [Amazon ElastiCache (Redis OSS)](https://aws.amazon.com/elasticache/), [Amazon Relational Database Service (RDS)](https://aws.amazon.com/rds/), and [AWS Secrets Manager](https://aws.amazon.com/secrets-manager/) services. Amazon ElastiCache enables multi-tenant distribution of application settings and prompt caching. Amazon RDS enables persistence of virtual API keys and other configuration settings provided by LiteLLM. Secrets Manager stores external model provider credentials and other sensitive settings securely.
7. LiteLLM and the API/middleware store application sends logs to the dedicated [Amazon S3](https://aws.amazon.com/s3) storage bucket for troubleshooting and access analysis. 

## How to deploy

```bash
# 1. Configure
cp .env.template .env
# Edit .env: set LITELLM_VERSION, BEDROCK_INFERENCE_REGION, VPC_CIDR_BLOCK, etc.
#   DEPLOYMENT_PLATFORM     — ECS or EKS
#   BEDROCK_INFERENCE_REGION — Region for Bedrock model calls (empty = deployment region)
#   ECS_VCPUS / ECS_MEMORY_GB — Compute resources
#   ALB_ALLOWED_PREFIX_LIST_ID — Restrict ALB ingress to a prefix list
#   USE_CLOUDFRONT / USE_ROUTE53 — Distribution options (see Distribution Options)

# 2. Customize model list
vi config/models.yaml

# 3. Deploy
./deploy.sh

# 4. Update models (after editing models.yaml)
./update-litellm-config.sh

# 5. Undeploy
./undeploy.sh
```

### LiteLLM Version Policy

Default: `v1.91.3`. LiteLLM adopts [SemVer](https://semver.org/) since v1.84.0:

- **MINOR** (v1.91.0 → v1.92.0): weekly release, includes new features
- **PATCH** (v1.91.0 → v1.91.3): hotfix only, no new features
- New features (e.g. GPT-5 on Bedrock) only land in the latest MINOR, not backported

See [LiteLLM versioning blog](https://docs.litellm.ai/blog/cleaner-release-versions) for details.

## API Endpoints

Two base URLs, selected by prefix. Within each, the `/v1` suffix is optional.

| Path | `data: [DONE]` | `session_id` | Backend |
|------|:---:|:---:|---------|
| `https://<host>/v1/chat/completions` | ✅ | — | LiteLLM native |
| `https://<host>/chat/completions` | ✅ | — | LiteLLM native |
| `https://<host>/plus/v1/chat/completions` | — | ✅ | Middleware |
| `https://<host>/plus/chat/completions` | — | ✅ | Middleware |

- **`https://<host>`** — standard OpenAI-compatible API (recommended for most clients).
- **`https://<host>/plus`** — middleware value-added features: chat history (`session_id` / `enable_history`), Bedrock Managed Prompts, native Bedrock interface (`/plus/bedrock/model/*`).

> **Migrating from < v1.1.0:** middleware features moved from the root paths to the `/plus` prefix. Standard OpenAI clients on `https://<host>/v1` are unaffected.

## Alerting

LiteLLM alerts (budget/spend, LLM exceptions, slow/hanging requests, daily & weekly reports, DB errors, model outages) can be delivered to **Slack** and/or **Lark (Feishu)**. Both are optional and independent — configure either, both, or neither in `.env`; `deploy.sh` wires everything automatically.

| `.env` variable | Effect |
|-----------------|--------|
| `SLACK_WEBHOOK_URL` | Send alerts to Slack (native LiteLLM alerting) |
| `LARK_WEBHOOK_URL` | Send alerts to a Lark custom-bot webhook |
| `LARK_WEBHOOK_SECRET` | Optional — Lark signature-verification secret |

When both `SLACK_WEBHOOK_URL` and `LARK_WEBHOOK_URL` are set, alerts fan out to both.

**How Lark works:** LiteLLM has no native Lark channel (Lark isn't Slack-compatible). The middleware exposes an internal endpoint (`/webhook/slack-to-lark`, module `middleware/lark_alerting.py`) that receives LiteLLM's Slack-format alerts, translates them into Lark interactive cards, and forwards them to `LARK_WEBHOOK_URL` (signing them if `LARK_WEBHOOK_SECRET` is set). This mirrors how LiteLLM officially integrates Discord/Teams via Slack-compatible webhooks. The endpoint is internal-only (localhost between containers) and is not exposed via the ALB/ingress.

**Lark bot security:** signature verification is recommended (set `LARK_WEBHOOK_SECRET`); IP allowlisting is not recommended (the middleware egresses via the NAT gateway EIP); custom keywords may not match interactive cards reliably.

## Distribution Options

Starting with version 1.1.0, this solution supports flexible deployment scenarios to meet various security and accessibility requirements. You can customize how your LiteLLM gateway is accessed based on your specific needs.

### Deployment Scenarios

#### Scenario 1: Default - Public with CloudFront (Recommended)
```bash
USE_CLOUDFRONT="true"
USE_ROUTE53="false"
PUBLIC_LOAD_BALANCER="true"
```

**Why choose this scenario:**
- Global performance with low-latency access via CloudFront's edge locations
- Enhanced security with AWS Shield Standard DDoS protection
- Simplified HTTPS management with CloudFront's default certificate
- Best option for public-facing AI services with global user base

**Security:**
- CloudFront IP filtering restricts ALB access to only CloudFront traffic
- WAF can be applied at the CloudFront level (requires global WAF)
- Simpler certificate management using CloudFront's default certificate

**Access URL:** `https://d1234abcdef.cloudfront.net`

#### Scenario 2: Custom Domain with CloudFront
```bash
USE_CLOUDFRONT="true"
USE_ROUTE53="true"
PUBLIC_LOAD_BALANCER="true"
HOSTED_ZONE_NAME="example.com"
RECORD_NAME="genai"
CERTIFICATE_ARN="arn:aws:acm:region:account:certificate/certificate-id"
```

**Why choose this scenario:**
- Brand consistency with your custom domain
- Professional appearance and SEO benefits
- Same global performance and security as Scenario 1

**Additional requirements:**
- Route53 hosted zone for your domain
- ACM certificate for your domain (must be in us-east-1 for CloudFront)

**Access URL:** `https://genai.example.com`

#### Scenario 3: Direct ALB Access (No CloudFront)
```bash
USE_CLOUDFRONT="false"
USE_ROUTE53="true"
PUBLIC_LOAD_BALANCER="true"
HOSTED_ZONE_NAME="example.com"
RECORD_NAME="genai"
CERTIFICATE_ARN="arn:aws:acm:region:account:certificate/certificate-id"
```

**Why choose this scenario:**
- Lower latency for single-region deployments
- Simplified architecture without CloudFront 
- Regional WAF can be directly applied to the ALB
- Cost savings by eliminating CloudFront distribution

**Security considerations:**
- No CloudFront layer means direct internet exposure of ALB
- WAF protection becomes particularly important
- ALB security group allows traffic from all IPs (0.0.0.0/0)

**Access URL:** `https://genai.example.com` (points directly to ALB)

#### Scenario 4: Private VPC Only
```bash
USE_CLOUDFRONT="false"
USE_ROUTE53="true"
PUBLIC_LOAD_BALANCER="false"
HOSTED_ZONE_NAME="example.internal"  # Often a private .internal domain
RECORD_NAME="genai"
CERTIFICATE_ARN="arn:aws:acm:region:account:certificate/certificate-id"
```

**Why choose this scenario:**
- Maximum security for internal enterprise applications
- Complete isolation from public internet
- Suitable for processing sensitive or proprietary data

**Access methods:**
- VPN connection to the VPC
- AWS Direct Connect
- VPC peering with corporate network
- Transit Gateway

**Security considerations:**
- No public internet access possible
- ALB security group only allows traffic from private subnet CIDRs
- Requires network connectivity to the VPC for access

**Access URL:** `https://genai.example.internal` (resolves only within VPC or connected networks)

### Configuration Quick Reference

| Parameter | Default | Description |
|-----------|---------|-------------|
| `USE_CLOUDFRONT` | `true` | Enables CloudFront distribution for global delivery |
| `USE_ROUTE53` | `false` | Enables Route53 for custom domain support |
| `PUBLIC_LOAD_BALANCER` | `true` | Deploys ALB in public subnets |
| `CLOUDFRONT_PRICE_CLASS` | `PriceClass_100` | CloudFront price class (100/200/All) |
| `HOSTED_ZONE_NAME` | `""` | Route53 hosted zone name for custom domain |
| `RECORD_NAME` | `""` | Record to create in Route53 (subdomain) |
| `CERTIFICATE_ARN` | `""` | ARN of ACM certificate for custom domain |

### Security Considerations

Each deployment scenario offers different security characteristics:

1. **CloudFront with public ALB (Default)**: 
   - ALB is in public subnets but protected by custom header authentication
   - Only traffic with the proper CloudFront secret header is allowed (except health check paths)
   - CloudFront provides an additional security layer with AWS Shield Standard DDoS protection
   - Best balance of accessibility and security for public services

2. **Direct ALB access (No CloudFront)**:
   - ALB directly accessible from internet
   - WAF protection is crucial for this deployment
   - Consider IP-based restrictions if possible

3. **Private VPC deployment**:
   - Highest security, no direct internet exposure
   - Requires VPN or Direct Connect for access
   - Consider for sensitive workloads or internal services

All scenarios maintain security best practices including:
- HTTPS for all communications with TLS 1.2+ 
- Security groups with principle of least privilege
- WAF protection against common attacks
- IAM roles with appropriate permissions

### CloudFront Authentication

When using CloudFront, a custom security mechanism is implemented:

1. CloudFront adds a secret header (`X-CloudFront-Secret`) to all requests sent to the ALB
2. The ALB has listener rules that verify this header before allowing access
3. Health check paths are specifically exempted to allow CloudFront origin health checks
4. The secret is stable across deployments (won't change unless explicitly changed)

This provides a robust defense against direct ALB access even if someone discovers your ALB's domain name. The secret is only displayed once after creation in the Terraform outputs and is marked as sensitive.

### AWS Services in this Guidance

| **AWS Service**                                                                                         | **Role**           | **Description**                                                                                             |
| ------------------------------------------------------------------------------------------------------- | ------------------ | ----------------------------------------------------------------------------------------------------------- |
| [Amazon Bedrock](https://aws.amazon.com/bedrock/)                                    | Core service       | Manages Single API access to multiple Foundational Models                                                   |
| [Amazon SageMaker AI](https://aws.amazon.com/sagemaker-ai/)                          | Core service       | Manages access to any Foundational Model deployed on Amazon SageMaker AI                                    |
| [Amazon Elastic Container Service](https://aws.amazon.com/ecs/) ( ECS)               | Core service       | Manages application platform and on-demand infrastructure for LiteLLM container orchestration.              |
| [Amazon Elastic Kubernetes Service](https://aws.amazon.com/eks/) ( EKS)              | Core service       | Manages Kubernetes control plane and compute nodes for LiteLLM container orchestration.                     |
| [Amazon Elastic Compute Cloud](https://aws.amazon.com/ec2/) (EC2)                    | Core service       | Provides compute instances for EKS compute nodes and runs containerized applications.                       |
| [Amazon Virtual Private Cloud](https://aws.amazon.com/vpc/) (VPC)                    | Core Service       | Creates an isolated network environment with public and private subnets across multiple Availability Zones. |
| [Amazon Web Applications Firewall](https://aws.amazon.com/waf/) (WAF)                | Core Service       | Protect guidance applications from common exploits                                                          |
| [Amazon Elastic Container Registry](http://aws.amazon.com/ecr/) (ECR)                | Supporting service | Stores and manages Docker container images for EKS deployments.                                             |
| [Elastic Load Balancer](https://aws.amazon.com/elasticloadbalancing/) (ALB)          | Supporting service | Distributes incoming traffic across multiple targets in the EKS cluster.                                    |
| [Amazon CloudFront](https://aws.amazon.com/cloudfront/)                              | Supporting service | Global content delivery network for improved performance and security.                                      |
| [Amazon Simple Storage Service ](https://aws.amazon.com/s3) (S3)                     | Supporting service | Provides persistent object storage for Applications logs and other related data.                            |
| [Amazon Relational Database Service ](https://aws.amazon.com/rds/) (RDS)             | Supporting service | Enables persistence of virtual API keys and other configuration settings provided by LiteLLM.               |
| [Amazon ElastiCache Service (Redis OSS) ](https://aws.amazon.com/elasticache/) (OSS) | Supporting service | Enables multi-tenant distribution of application settings and prompt caching.                               |
| [AWS Route 53](https://aws.amazon.com/route53/)                                      | Supporting Service | Optional DNS service for custom domain management                                                           |
| [AWS Identity and Access Management](https://aws.amazon.com/iam/) (IAM)              | Supporting service | Manages access to AWS services and resources securely, including ECS or EKS cluster access.                 |
| [AWS Certificate Manager](https://aws.amazon.com/certificate-manager/) (ACM)         | Security service   | Manages SSL/TLS certificates for secure communication within the cluster.                                   |
| [Amazon CloudWatch](https://aws.amazon.com/cloudwatch/)                              | Monitoring service | Collects and tracks metrics, logs, and events from ECS, EKS and other AWS resources provisoned in the guidance   |
| [AWS Secrets Manager](https://aws.amazon.com/secrets-manager/)                       | Management service | Manager stores external model provider credentials and other sensitive settings securely.                   |
| [AWS Key Management Service](https://aws.amazon.com/kms/) (KMS)                      | Security service   | Manages encryption keys for securing data in EKS and other AWS services.                                    |

**NOTE** For any guidance deployment, either Amazon ECS or EKS container orchestration platform can be used, but not both.

## Cost

### Cost Considerations

When implementing this guidance on AWS, it's important to understand the various factors that contribute to the overall cost. This section outlines the primary cost components and key factors that influence pricing.

### Cost Components

The total cost of running this solution can be broadly categorized into two main components:

1. **LLM Provider Costs**: These are the charges incurred for using services from LLM providers such as Amazon Bedrock, Amazon SageMaker AI, Anthropic, and others. Each provider has its own pricing model, typically based on factors like the number of tokens processed, model complexity, and usage volume.

2. **AWS Infrastructure Costs**: These are the costs associated with running the Gen AI Gateway proxy server on AWS infrastructure. This includes various AWS services and resources used to host and operate the solution.

### Key Factors Influencing AWS Infrastructure Costs

While the default configuration provides a starting point, the actual cost of running the LiteLLM-based proxy server on AWS can vary significantly based on your specific implementation and usage patterns. Some of the major factors that can impact scaling and cost include:

1. **Compute Instances**: The type and number of EC2 instances used to host the LiteLLM container as a proxy. Instance type selection affects both performance and cost.

2. **EBS Storage**: The type and size of EBS volumes attached to the EC2 instances can influence both performance and cost.

3. **Autoscaling Configuration**: The autoscaling policies configured for EKS/ECS clusters will affect how the solution scales in response to demand, impacting both performance and cost.

4. **Traffic Patterns**: The shape and distribution of LLM requests, including factors such as:

   - Request/response payload sizes
   - Tokens per minute (TPM)
   - Requests per minute (RPM)
   - Concurrency levels
   - Model latency (from downstream LLM providers)
   - Network latency between AWS and LLM providers

5. **Caching Configuration**: Effective caching can reduce the number of requests to LLM providers, potentially lowering costs but requiring additional resources.

6. **Database Storage**: The amount of storage required for managing virtual keys, organizations, teams, users, budgets, and per-request usage tracking.

7. **High Availability and Resiliency**: Configurations for load balancing, routing, and retries can impact both reliability and cost.

8. **Logging Level**: The configured logging level affects storage and potentially network egress costs.

9. **Networking Costs**: This includes data transfer charges and the cost of running NAT gateways for outgoing calls to LLM providers.

It's important to note that this is not an exhaustive list of cost factors, but rather highlights some of the major contributors to the overall cost of the solution.

### Customer Responsibility

While this implementation guide provides default configurations, customers are responsible for:

1. Configuring the solution to their optimal settings based on their specific use case and requirements.
2. Monitoring and managing the costs incurred from running the proxy server on AWS infrastructure.
3. Managing and optimizing the costs associated with their chosen LLM providers.

Customers should regularly review their AWS service usage patterns, adjust configurations as needed, and leverage AWS cost management tools to optimize their spending.

We recommend creating a [budget](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-create.html) 
through [AWS Cost Explorer](http://aws.amazon.com/aws-cost-management/aws-cost-explorer/) to
help manage costs. Prices are subject to change and also depend on model provider usage patterns/volume of data. For full details, please refer to the pricing webpage for each AWS service used in this guidance.

### Sample Cost tables

The following tables provide a sample cost breakdown for deploying this guidance on ECS and EKS container orchestration platforms with the default parameters in the `us-east-1` (N. Virginia) region for one month. These estimates are based on the AWS Pricing Calculator outputs for the full deployments as per guidance and are subject to changes in underlying services configuration.

**For ECS container orchestration platform**

| **AWS service**                          | Dimensions                                                                                        | Cost, month [USD] |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------- | ----------------- |
| Amazon Elastic Container Service (ECS)   | OS: Linux, CPU Architecture: ARM, 24 hours, 2 tasks per day, 4 GB Memory, 20 GB ephemeral storage | 115.33            |
| Amazon Virtual Private Cloud (VPC)       | 1 VPC, 4 subnets, 1 NAT Gateway, 1 public IPv4, 100 GB outbound data per month                    | 50.00             |
| Amazon Elastic Container Registry (ECR)  | 5 GB image storage/month                                                                          | 0.50              |
| Amazon Elastic Load Balancer (ALB)       | 1 ALB, 1 TB/month                                                                                 | 24.62             |
| Amazon Simple Storage Service (S3)       | 100 GB/month                                                                                      | 7.37              |
| Amazon Relational Database Service (RDS) | 2 db.t3.micro nodes, 100% utilization, multi-AZ, 2 vCPU,1 GiB Memory                               | 98.26             |
| Amazon ElastiCache Service (Redis OSS)   | 2 cache.t3.micro nodes, 2 vCPU, 0.5 GiB Memory, Upto 5 GB Network performance, 100% utilization   | 24.82             |
| Amazon Route 53                          | 1 hosted zone, 1 million standard queries/month                                                    | 26.60             |
| Amazon CloudWatch                        | 25 metrics to preserve                                                                            | 12.60             |
| AWS Secrets Manager                      | 5 secrets, 30 days, 1 million API calls per month                                                 | 7.00              |
| AWS Key Management Service (KMS)         | 1 key, 1 million symmertic requests                                                                | 4.00              |
| AWS WAF                                  | 1 web ACL, 2 rules                                                                                | 7.00              |
| AWS Certificate Manager                  | 1 Certificate                                                                                     | free              |
| **TOTAL**                                |                                                                                                   | **$378.10/month** |

For detailed cost estimates for deployment on ECS platform, it is recommended to create an AWS Price calculator like [this:](https://calculator.aws/#/estimate?id=8bce7fe949694f4ddbb08c9974ddcda9d13b1398)

**For EKS container orchestration platform:**

| **AWS service**                          | Dimensions                                                                                      | Cost, month [USD] |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------- | ----------------- |
| Amazon Elastic Kubernetes Service (EKS)  | 1 control plane                                                                                 | 73.00             |
| Amazon Elastic Compute Cloud (EC2)       | EKS Compute Nodes, 2 nodes t4g.medium                                                           | 49.06             |
| Amazon Virtual Private Cloud (VPC)       | 1 VPC, 4 subnets, 1 NAT Gateway, 1 public IPv4, 100 GB outbound data per month                  | 50.00             |
| Amazon Elastic Container Registry (ECR)  | 5 GB image storage/month                                                                        | 0.50              |
| Amazon Elastic Load Balancer (ALB)       | 1 ALB, 1 TB/month                                                                               | 24.62             |
| Amazon Simple Storage Service (S3)       | 100 GB/month                                                                                    | 7.37              |
| Amazon Relational Database Service (RDS) | 2 db.t3.micro nodes, 100% utilization, multi-AZ, 2 vCPU,1 GiB Memory                             | 98.26             |
| Amazon ElastiCache Service (Redis OSS)   | 2 cache.t3.micro nodes, 2 vCPU, 0.5 GiB Memory, Upto 5 GB Network performance, 100% utilization | 24.82             |
| Amazon Route 53                          | 1 hosted zone, 1 million standard queries/month                                                  | 26.60             |
| Amazon CloudWatch                        | 25 metrics to preserve                                                                          | 12.60             |
| AWS Secrets Manager                      | 5 secrets, 30 days, 1 million API calls per month                                               | 7.00              |
| AWS Key Management Service (KMS)         | 1 key, 1 million symmertic requests                                                              | 4.00              |
| AWS WAF                                  | 1 web ACL, 2 rules                                                                              | 7.00              |
| AWS Certificate Manager                  | 1 Certificate                                                                                   | free              |
| **TOTAL**                                |                                                                                                 | **$384.83/month** |

For detailed cost estimates for deployment on EKS platform, it is recommended to create an AWS Price calculator like [this:](https://calculator.aws/#/estimate?id=2e331688341278d6e3e1a8b38c8ba76756e71f08)

## Security

When you build systems on AWS infrastructure, security responsibilities are shared between you and AWS. This [shared responsibility model](https://aws.amazon.com/compliance/shared-responsibility-model/) reduces your operational burden because AWS operates, manages, and controls the components including the host operating system, the virtualization layer, and the physical security of the facilities in which the services operate. For more information about AWS security, visit [AWS Cloud Security](http://aws.amazon.com/security/).

This guidance implements several security best practices and AWS services to enhance the security posture of your ECS and EKS Clusters. Here are the key security components and considerations:

### Identity and Access Management (IAM)

- **IAM Roles**: The architecture deploys dedicated IAM roles (`litellm-stack-developers`, `litellm-stack-operators`) to manage access to ECS or EKS cluster resources. This follows the principle of least privilege, ensuring users and services have only the permissions necessary to perform their tasks.
- **EKS Managed Node Groups**: These groups use created IAM roles (`litellm-stack-eks-nodegroup-role`) with specific permissions required for nodes to join the cluster and for pods to access AWS services.

### Network Security

- **Amazon VPC**: ECS or EKS clusters are deployed within a VPC (newly created or custom specified in guidance deployment configuration) with public and private subnets across multiple Availability Zones, providing network isolation.
- **Security Groups**: Security groups are typically used to control inbound and outbound traffic to EC2 instances and other resources within the VPC.
- **NAT Gateways**: Deployed in public subnets to allow outbound internet access for resources in private subnets while preventing inbound access from the internet.

### Data Protection

- **Amazon EBS Encryption**: EBS volumes used by EC2 instances for EKS compute nodes are typically encrypted to protect data at rest.
- **AWS Key Management Service (KMS)**: used for managing encryption keys for various services, including EBS volume encryption.
- **AWS Secrets manager**: used for stores external model providers credentials and other sensitive settings securely.

### Kubernetes-specific Security

- **Kubernetes RBAC**: Role-Based Access Control is implemented within the EKS cluster to manage fine-grained access to Kubernetes resources.
- **AWS Certificate Manager**: Integrated to manage SSL/TLS certificates for secure communication within the clusters.
- **AWS Identity and Access Manager**: used for role/policy based access to AWS services and resources, including ECS or EKS cluster resource access

### Monitoring and Logging

- **Amazon CloudWatch**: Used for monitoring and logging of AWS resources and applications running on the EKS cluster.

### Container Security

- **Amazon ECR**: Stores container images in a secure, encrypted repository. It includes vulnerability scanning to identify security issues in your container images.

### Secrets Management

- **AWS Secrets Manager**: Secrets Manager stores external model provider credentials and other sensitive settings securely.

### Additional Security Considerations

- Regularly update and patch ECS or EKS clusters, compute nodes, and container images.
- Implement network policies to control pod-to-pod communication within the cluster.
- Use Pod Security Policies or Pod Security Standards to enforce security best practices for pods.
- Implement proper logging and auditing mechanisms for both AWS and Kubernetes resources.
- Regularly review and rotate IAM and Kubernetes RBAC permissions.

### Supported AWS Regions

This guidance can be deployed in any AWS Region where Amazon Bedrock is available. See [Amazon Bedrock supported regions](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-regions.html) for the full list.

> **Note**: AWS China Regions (cn-north-1, cn-northwest-1) are not supported due to differences in service availability.


### Quotas

Ensure you have sufficient [AWS service quotas](https://docs.aws.amazon.com/general/latest/gr/aws_service_limits.html) for ECS/EKS, VPC, ALB, RDS, and ElastiCache in your deployment region.

## Open Source Library

For detailed information about the open source libraries used in this application, please refer to the [ATTRIBUTION](ATTRIBUTION.md) file.

## Notices 

Customers are responsible for making their own independent assessment of the information in this Guidance. This Guidance: (a) is for informational purposes only, (b) represents AWS current product offerings and practices, which are subject to change without notice, and (c) does not create any commitments or assurances from AWS and its affiliates, suppliers or licensors. AWS products or services are provided "as is" without warranties, representations, or conditions of any kind, whether express or implied. AWS responsibilities and liabilities to its customers are controlled by AWS agreements, and this Guidance is not part of, nor does it modify, any agreement between AWS and its customers.
