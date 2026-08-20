variable "region" {
  description = "AWS Region used by the GitHub Actions Bedrock clients."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.region == "us-east-1"
    error_message = "The checked-in model set and Nova inference-profile policy are verified only for us-east-1."
  }
}

variable "github_owner" {
  description = "GitHub organization that owns the existing repository."
  type        = string
  default     = "IkkaLabs"
}

variable "github_repository" {
  description = "Name of the existing GitHub repository allowed to assume the role."
  type        = string
  default     = "praas"
}

variable "github_owner_id" {
  description = "Immutable GitHub organization ID used in OIDC subjects for repositories created after July 15, 2026."
  type        = number
  default     = 246152865
}

variable "github_repository_id" {
  description = "Immutable GitHub repository ID used in the OIDC trust policy."
  type        = number
  default     = 1340021625
}

variable "github_oidc_provider_arn" {
  description = "ARN of an existing token.actions.githubusercontent.com IAM OIDC provider. Leave null to create it."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.github_oidc_provider_arn == null ||
      can(regex("^arn:[^:]+:iam::[0-9]{12}:oidc-provider/token\\.actions\\.githubusercontent\\.com$", var.github_oidc_provider_arn))
    )
    error_message = "github_oidc_provider_arn must be the ARN for token.actions.githubusercontent.com."
  }
}

variable "role_name" {
  description = "Name of the IAM role assumed by the praas review workflows."
  type        = string
  default     = "praas-github-actions-bedrock"
}

variable "tags" {
  description = "Tags applied to Terraform-managed AWS resources."
  type        = map(string)
  default = {
    ManagedBy  = "Terraform"
    Project    = "praas"
    Repository = "IkkaLabs/praas"
  }
}

variable "manage_github" {
  description = "When true, use the GitHub provider (authenticated via the GITHUB_TOKEN environment variable) to set the AWS_ROLE_ARN Actions secret and create the review labels on the repository. Leave false to manage AWS only."
  type        = bool
  default     = false
}

variable "review_labels" {
  description = "PR labels that trigger the review workflows, mapped to a six-digit hex color. Created only when manage_github is true."
  type        = map(string)
  default = {
    "praas-gemini"            = "1f6feb"
    "praas-bedrock-nova"      = "0e8a16"
    "praas-bedrock-qwen"      = "0e8a16"
    "praas-bedrock-kimi-k2.5" = "0e8a16"
    "praas-bedrock-deepseek"  = "0e8a16"
    "praas-local-qwen35-9b"   = "5319e7"
    "praas-local-gemma4-12b"  = "5319e7"
    "praas-all"               = "d93f0b"
  }
}
