output "github_actions_role_arn" {
  description = "IAM role ARN to store as the AWS_ROLE_ARN GitHub Actions secret."
  value       = aws_iam_role.github_actions_bedrock.arn
}

output "github_oidc_provider_arn" {
  description = "GitHub Actions OIDC provider used by the role."
  value       = local.github_oidc_provider_arn
}

output "allowed_oidc_subjects" {
  description = "Exact GitHub OIDC subjects accepted by the role trust policy."
  value       = local.allowed_oidc_subjects
}

output "bedrock_resource_arns" {
  description = "Bedrock inference profile and foundation-model ARNs granted to the workflow role."
  value = concat(
    local.direct_model_arns,
    [local.nova_inference_profile_arn],
    local.nova_model_arns,
  )
}
