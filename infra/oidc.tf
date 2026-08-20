locals {
  github_oidc_provider_arn = var.github_oidc_provider_arn != null ? var.github_oidc_provider_arn : aws_iam_openid_connect_provider.github[0].arn

  # GitHub changed the default OIDC subject for newly created repositories on
  # July 15, 2026. Keep the exact legacy subject for compatibility, plus the
  # exact immutable-ID subject used by this repository. No owner/repo wildcards.
  allowed_oidc_subjects = [
    "repo:${var.github_owner}/${var.github_repository}:*",
    "repo:${var.github_owner}@${var.github_owner_id}/${var.github_repository}@${var.github_repository_id}:*",
  ]

  invoke_actions = [
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream",
  ]

  direct_model_ids = [
    "deepseek.v3.2",
    "moonshotai.kimi-k2.5",
    "qwen.qwen3-coder-next",
  ]

  nova_model_id              = "amazon.nova-pro-v1:0"
  nova_inference_profile_id  = "us.amazon.nova-pro-v1:0"
  nova_destination_regions   = ["us-east-1", "us-east-2", "us-west-2"]
  nova_inference_profile_arn = "arn:${data.aws_partition.current.partition}:bedrock:${var.region}:${data.aws_caller_identity.current.account_id}:inference-profile/${local.nova_inference_profile_id}"
  direct_model_arns = [
    for model_id in local.direct_model_ids :
    "arn:${data.aws_partition.current.partition}:bedrock:${var.region}::foundation-model/${model_id}"
  ]
  nova_model_arns = [
    for destination_region in local.nova_destination_regions :
    "arn:${data.aws_partition.current.partition}:bedrock:${destination_region}::foundation-model/${local.nova_model_id}"
  ]
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.github_oidc_provider_arn == null ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_actions_trust" {
  statement {
    sid     = "GitHubActionsOidc"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.allowed_oidc_subjects
    }
  }
}

resource "aws_iam_role" "github_actions_bedrock" {
  name                 = var.role_name
  description          = "Allows IkkaLabs/praas GitHub Actions to invoke the configured Bedrock review models."
  assume_role_policy   = data.aws_iam_policy_document.github_actions_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "bedrock_invoke" {
  statement {
    sid       = "InvokeRegionalReviewModels"
    effect    = "Allow"
    actions   = local.invoke_actions
    resources = local.direct_model_arns
  }

  statement {
    sid       = "InvokeNovaInferenceProfile"
    effect    = "Allow"
    actions   = local.invoke_actions
    resources = [local.nova_inference_profile_arn]
  }

  statement {
    sid       = "InvokeNovaThroughProfile"
    effect    = "Allow"
    actions   = local.invoke_actions
    resources = local.nova_model_arns

    condition {
      test     = "StringLike"
      variable = "bedrock:InferenceProfileArn"
      values   = [local.nova_inference_profile_arn]
    }
  }
}

resource "aws_iam_role_policy" "bedrock_invoke" {
  name   = "invoke-praas-review-models"
  role   = aws_iam_role.github_actions_bedrock.id
  policy = data.aws_iam_policy_document.bedrock_invoke.json
}
