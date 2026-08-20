# Setting up praas infrastructure

This directory contains the Terraform configuration to provision AWS authentication and Amazon Bedrock IAM permissions for praas review workflows.

By default, Terraform manages AWS IAM resources only. When `manage_github = true` is set, Terraform also uses the GitHub provider to configure the `AWS_ROLE_ARN` repository secret and create review labels directly in GitHub.

## Provisioned infrastructure

- **IAM OIDC Identity Provider**: Registers `token.actions.githubusercontent.com` in AWS (or reuses an existing provider ARN if specified).
- **IAM Role for GitHub Actions**: Creates an IAM role with a trust policy scoped to your repository by owner login, numeric owner ID, and numeric repository ID.
- **Amazon Bedrock IAM Permissions**: Attaches inline policies authorizing model invocations in `us-east-1` for Amazon Nova Pro, Qwen3 Coder Next, Kimi K2.5, and DeepSeek V3.2.
- **GitHub Repository Management (Optional)**: Creates `praas-*` review labels and sets the `AWS_ROLE_ARN` secret in your repository when `manage_github = true`.

## Terraform variables

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `github_owner` | `string` | — | GitHub organization or user account name |
| `github_repository` | `string` | — | Target GitHub repository name |
| `github_owner_id` | `number` | — | Numeric ID of the GitHub owner or organization |
| `github_repository_id` | `number` | — | Numeric ID of the GitHub repository |
| `manage_github` | `bool` | `false` | When `true`, provisions GitHub secrets and review labels via the GitHub Terraform provider |
| `github_oidc_provider_arn` | `string` | `""` | Existing GitHub Actions IAM OIDC provider ARN. If empty, Terraform creates one |
| `aws_region` | `string` | `"us-east-1"` | AWS Region for Bedrock model access |

## Prerequisites

### Terraform

Install Terraform 1.6 or higher from [developer.hashicorp.com/terraform/install](https://developer.hashicorp.com/terraform/install).

Verify the installation:

```bash
terraform version
```

### AWS credentials

You need an AWS account with permissions to create IAM roles, IAM OIDC providers, and inline policies.

Configure credentials using the AWS CLI:

```bash
aws configure
```

You will be prompted for:
- **AWS Access Key ID**: Go to AWS Console → IAM → Your user → Security credentials → Create access key
- **AWS Secret Access Key**: Displayed once when creating the key — copy it immediately
- **Default region**: Enter `us-east-1`
- **Default output format**: Press Enter to skip

Alternatively, export credentials as environment variables:

```bash
export AWS_ACCESS_KEY_ID="your-access-key-id"
```

```bash
export AWS_SECRET_ACCESS_KEY="your-secret-access-key"
```

```bash
export AWS_DEFAULT_REGION="us-east-1"
```

### Amazon Bedrock model access

In the AWS Console:

1. Go to **Amazon Bedrock → Model access → Manage model access**
2. Enable: Amazon Nova Pro, Qwen3 Coder Next, Kimi K2.5, DeepSeek V3.2
3. Click **Save changes** and wait for status to become **Access granted**

### GitHub Personal Access Token

Required only when `manage_github = true`. This token lets Terraform write the `AWS_ROLE_ARN` secret and create review labels in your repository.

1. Go to **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. Click **Generate new token**
3. Set **Resource owner** to your account or organization
4. Under **Repository access**, select **Only select repositories** and choose your target repository
5. Under **Permissions**, grant:
   - **Administration**: Read and write
   - **Secrets**: Read and write
   - **Actions**: Read-only
   - **Metadata**: Read-only (required, enabled automatically)
6. Click **Generate token** and copy the value — you will not see it again

Export the token before running Terraform:

```bash
export GITHUB_TOKEN="github_pat_your_token_here"
```

## Finding your variable values

### github_owner and github_repository

`github_owner` is your GitHub username or organization name (e.g. `octocat` or `my-org`). `github_repository` is the repository name (e.g. `my-project`).

### github_owner_id

For a personal account:

```bash
gh api /users/<your-username> --jq .id
```

For an organization:

```bash
gh api /orgs/<your-org> --jq .id
```

### github_repository_id

```bash
gh api /repos/<owner>/<repo> --jq .id
```

## Execution guide

**1.** Navigate to the `infra/` directory:

```bash
cd infra
```

**2.** Create a local variables file from the provided example:

```bash
cp terraform.tfvars.example local.auto.tfvars
```

**3.** Open `local.auto.tfvars` and fill in your values:

```hcl
github_owner         = "<owner>"
github_repository    = "<repo>"
github_owner_id      = <numeric-owner-id>
github_repository_id = <numeric-repo-id>
manage_github        = true

# Optional: reuse an existing IAM OIDC provider if already registered in this AWS account
# github_oidc_provider_arn = "arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com"
```

**4.** Export your GitHub token (required when `manage_github = true`):

```bash
export GITHUB_TOKEN="github_pat_your_token_here"
```

**5.** Download provider plugins:

```bash
terraform init
```

**6.** Check for configuration syntax errors:

```bash
terraform validate
```

**7.** Preview what will be created — review this output before applying:

```bash
terraform plan
```

**8.** Create the infrastructure:

```bash
terraform apply
```

This provisions the IAM OIDC provider, IAM role, Bedrock permissions, the `AWS_ROLE_ARN` GitHub secret, and all `praas-*` review labels.

**9.** If `manage_github = false`, set the role ARN manually in your GitHub repository under **Settings → Secrets and variables → Actions → New repository secret**, with name `AWS_ROLE_ARN` and value from:

```bash
terraform output -raw github_actions_role_arn
```

## How to tear it down

To remove all provisioned IAM roles and GitHub labels:

```bash
terraform destroy
```

> [!WARNING]
> `terraform destroy` will remove the GitHub OIDC Identity Provider in AWS only if it was created by this Terraform workspace. If other roles in your AWS account share the same OIDC provider, specify `github_oidc_provider_arn` in your variables to prevent Terraform from deleting a shared resource.

## Managing any GitHub repository with Terraform and a PAT

The `manage_github` path above uses the [`integrations/github`](https://registry.terraform.io/providers/integrations/github/latest) provider to manage GitHub itself — repositories, Actions secrets, labels, branch protection — authenticated with a personal access token (PAT). This section explains the underlying mechanism generically, for adapting it to manage other repositories beyond the `manage_github` variable's built-in scope.

### 1. Create a PAT

GitHub → Settings → Developer settings → Personal access tokens.

- **Fine-grained (preferred):** scope the token to the specific repositories or organization it will manage, then grant only the permissions you need:
  - `Administration` — create repositories, manage settings, labels, branch protection.
  - `Secrets` and `Actions` — read and write Actions secrets and variables.
  - `Contents` — manage files, branches, releases.
  - `Metadata` — read (required by the provider).
- **Classic:** the `repo` scope covers most repository management; add `admin:org` for organization resources and `workflow` to edit workflow files.

Prefer fine-grained tokens and the least set of permissions that lets the plan apply.

### 2. Provide the token by environment variable

The provider reads the token from `GITHUB_TOKEN` (or `GITHUB_APP_*` for a GitHub App). Never hard-code it in `.tf` or `.tfvars` files.

```bash
export GITHUB_TOKEN="github_pat_xxx"
```

### 3. Configure the provider

```hcl
terraform {
  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
  }
}

# token comes from GITHUB_TOKEN; owner is the org or user that owns the repos
provider "github" {
  owner = "IkkaLabs"
}
```

### 4. Declare resources

```hcl
# Create a repository
resource "github_repository" "example" {
  name       = "example-repo"
  visibility = "private"
}

# Set an Actions secret (for example, an OIDC role ARN produced elsewhere)
resource "github_actions_secret" "aws_role_arn" {
  repository      = github_repository.example.name
  secret_name     = "AWS_ROLE_ARN"
  plaintext_value = var.aws_role_arn # mark the variable sensitive
}

# Create a label
resource "github_issue_label" "review" {
  repository = github_repository.example.name
  name       = "needs-review"
  color      = "0e8a16"
}
```

### 5. Apply

```bash
terraform init
terraform plan
terraform apply
```

### Managing a repository that already exists

Do not declare a `github_repository` for a repo you did not create with this stack — import it instead, so Terraform adopts it rather than trying to create a duplicate:

```bash
terraform import github_repository.example example-repo
```

Individual resources import the same way — for example an existing label:

```bash
terraform import 'github_issue_label.review' example-repo:needs-review
```

### Cautions

- **State holds secrets in plaintext.** Any `plaintext_value` and the PAT's effects live in `terraform.tfstate`. Treat state as sensitive: use a protected remote backend (for example S3 with a lock table), and never commit it. The `.gitignore` here already excludes `*.tfstate*` and `*.tfvars`.
- **A PAT is a long-lived credential.** It carries the token owner's access. Scope it tightly, rotate it, and store it in a secret manager — not your shell history. Where the target supports OIDC (as this repo's AWS side does), prefer short-lived OIDC federation over a standing PAT.
- **`owner` matters.** For an organization, `owner` must be the org login and the PAT must have organization access; otherwise resources are created under the token owner's account.
