# Optional GitHub-side wiring, gated by var.manage_github.
#
# When enabled, Terraform authenticates to GitHub with the PAT in the
# GITHUB_TOKEN environment variable and:
#   - writes the created IAM role ARN into the AWS_ROLE_ARN Actions secret, so
#     the manual `gh secret set` step in the README is no longer required; and
#   - creates the PR labels that trigger the review workflows.
#
# The PAT needs, on IkkaLabs/praas-test: Administration and Secrets (to write
# the Actions secret) and Actions/Metadata read. Leave manage_github false to
# manage only AWS and set the secret and labels by hand.

resource "github_actions_secret" "aws_role_arn" {
  count = var.manage_github ? 1 : 0

  repository      = var.github_repository
  secret_name     = "AWS_ROLE_ARN"
  plaintext_value = aws_iam_role.github_actions_bedrock.arn
}

resource "github_issue_label" "review" {
  for_each = var.manage_github ? var.review_labels : {}

  repository = var.github_repository
  name       = each.key
  color      = each.value
}
