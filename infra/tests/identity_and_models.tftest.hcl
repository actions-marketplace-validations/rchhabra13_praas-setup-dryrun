mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
      arn        = "arn:aws:iam::123456789012:user/terraform-test"
      user_id    = "AIDATEST"
    }
  }

  mock_data "aws_partition" {
    defaults = {
      partition          = "aws"
      dns_suffix         = "amazonaws.com"
      reverse_dns_prefix = "com.amazonaws"
    }
  }
}

override_data {
  target = data.aws_iam_policy_document.github_actions_trust
  values = {
    json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
  }
}

override_data {
  target = data.aws_iam_policy_document.bedrock_invoke
  values = {
    json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
  }
}

run "repository_identity_and_model_scope" {
  command = plan

  assert {
    condition = contains(
      output.allowed_oidc_subjects,
      "repo:IkkaLabs@246152865/praas-test@1324415839:*",
    )
    error_message = "The trust policy must include praas-test's exact immutable GitHub OIDC subject."
  }

  assert {
    condition = contains(
      output.allowed_oidc_subjects,
      "repo:IkkaLabs/praas-test:*",
    )
    error_message = "The trust policy must retain the exact legacy praas-test OIDC subject."
  }

  assert {
    condition = alltrue([
      for model_id in [
        "deepseek.v3.2",
        "moonshotai.kimi-k2.5",
        "qwen.qwen3-coder-next",
        "amazon.nova-pro-v1:0",
        ] : anytrue([
          for resource_arn in output.bedrock_resource_arns : strcontains(resource_arn, model_id)
      ])
    ])
    error_message = "The role policy must cover every Bedrock model used by the repository workflows."
  }

  assert {
    condition = contains(
      output.bedrock_resource_arns,
      "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.amazon.nova-pro-v1:0",
    )
    error_message = "The role policy must include the account-scoped US Nova inference profile."
  }
}
