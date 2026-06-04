###############################################################################
# (10) WAFv2 Web ACL Association
###############################################################################
resource "aws_wafv2_web_acl_association" "litellm_waf" {
  count        = var.enable_waf ? 1 : 0
  resource_arn = aws_lb.this.arn
  web_acl_arn  = var.wafv2_acl_arn
}
