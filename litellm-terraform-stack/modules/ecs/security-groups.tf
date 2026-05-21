###############################################################################
# (7) Security Groups & Ingress for Redis and RDS
###############################################################################

# CloudFront security is implemented using origin custom headers instead of IP ranges
# This avoids hitting AWS security group rule limits (60 rules per security group)
# CloudFront has hundreds of IP ranges globally, which would exceed the limit
# Security group for ECS Service tasks
resource "aws_security_group" "ecs_service_sg" {
  name        = "${var.name}-service-sg"
  description = "Security group for ECS Fargate service"
  vpc_id      = var.vpc_id

  egress {
    from_port        = 0
    to_port          = 0
    protocol         = "-1"  # "-1" represents all protocols
    cidr_blocks      = ["0.0.0.0/0"]
    description      = "Allow all outbound traffic by default"
  }
}

resource "aws_security_group_rule" "alb_ingress_4000" {
  type                     = "ingress"
  from_port                = 4000
  to_port                  = 4000
  protocol                 = "tcp"
  security_group_id        = aws_security_group.ecs_service_sg.id
  source_security_group_id = aws_security_group.alb_sg.id
  description              = "Allow Load Balancer to ECS"
}

resource "aws_security_group_rule" "alb_ingress_3000" {
  type                     = "ingress"
  from_port                = 3000
  to_port                  = 3000
  protocol                 = "tcp"
  security_group_id        = aws_security_group.ecs_service_sg.id
  source_security_group_id = aws_security_group.alb_sg.id
  description              = "Allow Load Balancer to ECS"
}


# Allow ECS tasks to connect to Redis
resource "aws_security_group_rule" "redis_ingress" {
  type                     = "ingress"
  from_port                = 6379
  to_port                  = 6379
  protocol                 = "tcp"
  security_group_id        = var.redis_security_group_id
  source_security_group_id = aws_security_group.ecs_service_sg.id
  description              = "Allow ECS tasks to connect to Redis"
}

# Allow ECS tasks to connect to RDS
resource "aws_security_group_rule" "db_ingress" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = var.db_security_group_id
  source_security_group_id = aws_security_group.ecs_service_sg.id
  description              = "Allow ECS tasks to connect to RDS"
}

resource "aws_security_group" "alb_sg" {
  name        = "${var.name}-alb-sg"
  description = "Security group for ALB"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.name}-alb-sg"
    SecurityModel = var.use_cloudfront ? "CloudFront-Protected" : (var.public_load_balancer ? "Public-WAF-Protected" : "Private-VPC-Only")
  }

  # Allow all outbound
  egress {
    description = "Allow all outbound"
    protocol    = -1
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# AWS-managed prefix list for CloudFront origin-facing IPs
data "aws_ec2_managed_prefix_list" "cloudfront" {
  count = var.use_cloudfront ? 1 : 0
  name  = "com.amazonaws.global.cloudfront.origin-facing"
}

# --- ALB Ingress Rules ---

# Scenario 1: CloudFront enabled - restrict to CloudFront IPs only
resource "aws_security_group_rule" "alb_ingress_http_cloudfront" {
  count             = var.use_cloudfront ? 1 : 0
  type              = "ingress"
  from_port         = 80
  to_port           = 80
  protocol          = "tcp"
  security_group_id = aws_security_group.alb_sg.id
  prefix_list_ids   = [data.aws_ec2_managed_prefix_list.cloudfront[0].id]
  description       = "HTTP from CloudFront origin-facing IPs only"
}

# Scenario 2: No CloudFront, custom prefix list provided
resource "aws_security_group_rule" "alb_ingress_https_prefix_list" {
  count             = !var.use_cloudfront && var.alb_allowed_prefix_list_id != "" ? 1 : 0
  type              = "ingress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  security_group_id = aws_security_group.alb_sg.id
  prefix_list_ids   = [var.alb_allowed_prefix_list_id]
  description       = "HTTPS from allowed prefix list"
}

resource "aws_security_group_rule" "alb_ingress_http_prefix_list" {
  count             = !var.use_cloudfront && var.alb_allowed_prefix_list_id != "" ? 1 : 0
  type              = "ingress"
  from_port         = 80
  to_port           = 80
  protocol          = "tcp"
  security_group_id = aws_security_group.alb_sg.id
  prefix_list_ids   = [var.alb_allowed_prefix_list_id]
  description       = "HTTP from allowed prefix list"
}

# Scenario 3: No CloudFront, no prefix list - fallback to CIDR
resource "aws_security_group_rule" "alb_ingress_https_cidr" {
  count             = !var.use_cloudfront && var.alb_allowed_prefix_list_id == "" ? 1 : 0
  type              = "ingress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  security_group_id = aws_security_group.alb_sg.id
  cidr_blocks       = var.public_load_balancer ? ["0.0.0.0/0"] : var.private_subnets_cidr_blocks
  description       = "HTTPS traffic"
}

resource "aws_security_group_rule" "alb_ingress_http_cidr" {
  count             = !var.use_cloudfront && var.alb_allowed_prefix_list_id == "" ? 1 : 0
  type              = "ingress"
  from_port         = 80
  to_port           = 80
  protocol          = "tcp"
  security_group_id = aws_security_group.alb_sg.id
  cidr_blocks       = var.public_load_balancer ? ["0.0.0.0/0"] : var.private_subnets_cidr_blocks
  description       = "HTTP traffic"
}
