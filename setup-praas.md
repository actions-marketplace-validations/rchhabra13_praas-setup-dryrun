# Setting up praas infrastructure

`infra/` is the Terraform configuration that provisions AWS authentication and Amazon Bedrock IAM permissions for the `praas-bedrock-*` review workflows, and optionally the GitHub-side wiring (labels, secret) those workflows need.

By default, Terraform manages **AWS IAM resources only**. When `manage_github = true` is set, it also uses the GitHub provider to create the `praas-*` review labels and write the `AWS_ROLE_ARN` repository secret.

The shipped variable defaults target **this repository, `IkkaLabs/praas`, itself**. If you're wiring praas into a *different* repository, override every value under [Target repository](#target-repository) below. Applying with the defaults unmodified scopes the IAM trust policy to `IkkaLabs/praas`, not your repo.

## What gets provisioned

- **IAM OIDC Identity Provider**: registers `token.actions.githubusercontent.com` in AWS, or reuses an existing provider ARN if `github_oidc_provider_arn` is set (an AWS account can only have one provider per URL).
- **IAM Role** (`aws_iam_role.github_actions_bedrock`): trust policy scoped to your repository by owner login, numeric owner ID, and numeric repository ID (both the legacy and post-2026-07-15 OIDC subject formats are allowed, see `infra/oidc.tf`).
- **Bedrock invoke permissions**: inline policy granting `bedrock:InvokeModel`/`InvokeModelWithResponseStream` on exactly the four review models: DeepSeek V3.2, Kimi K2.5, Qwen3 Coder Next (direct foundation-model ARNs), and Amazon Nova Pro (via its cross-region inference profile, resolved across `us-east-1`/`us-east-2`/`us-west-2`).
- **GitHub labels and secret** (only when `manage_github = true`): the 8 `praas-*` review labels and the `AWS_ROLE_ARN` Actions secret, set to the IAM role's ARN.

## Terraform variables

| Variable | Type | Default | Notes |
| --- | --- | --- | --- |
| `region` | `string` | `"us-east-1"` | AWS region for the Bedrock clients. **Not freely configurable.** A `validation` block rejects anything but `"us-east-1"`, because the checked-in model set and the Nova inference-profile policy are only verified there. |
| `github_owner` | `string` | `"IkkaLabs"` | Owner of the target repository. |
| `github_repository` | `string` | `"praas"` | Target repository name. |
| `github_owner_id` | `number` | `246152865` | Immutable numeric ID of `github_owner`. `gh api /orgs/<owner> --jq .id` (or `/users/<owner>` for a personal account). |
| `github_repository_id` | `number` | `1340021625` | Immutable numeric ID of `github_repository`. `gh api /repos/<owner>/<repo> --jq .id`. |
| `github_oidc_provider_arn` | `string`, nullable | `null` | Set to an existing `token.actions.githubusercontent.com` OIDC provider ARN to reuse it instead of creating a new one. Validated against that exact provider URL if set. |
| `role_name` | `string` | `"praas-github-actions-bedrock"` | Name of the IAM role the workflows assume. |
| `tags` | `map(string)` | `{ManagedBy="Terraform", Project="praas", Repository="IkkaLabs/praas"}` | Applied to every AWS resource this stack creates, via `default_tags` on the AWS provider. |
| `manage_github` | `bool` | `false` | When `true`, also creates the review labels and the `AWS_ROLE_ARN` secret via the GitHub provider (needs `GITHUB_TOKEN`). |
| `review_labels` | `map(string)` | the 8 `praas-*` labels → hex colors (see `infra/variables.tf`) | Only created when `manage_github = true`. Matches the label table in the root [README.md](README.md). |

### Outputs

| Output | Description |
| --- | --- |
| `github_actions_role_arn` | The IAM role ARN. This is the value to store as the `AWS_ROLE_ARN` GitHub secret. |
| `github_oidc_provider_arn` | The OIDC provider ARN actually in use (created or reused). |
| `allowed_oidc_subjects` | The exact GitHub OIDC subjects the role's trust policy accepts. |
| `bedrock_resource_arns` | Every Bedrock model/inference-profile ARN the role is granted `InvokeModel` on. |

## Prerequisites

### Terraform

Requires Terraform `>= 1.6.0`. Install from [developer.hashicorp.com/terraform/install](https://developer.hashicorp.com/terraform/install), then verify:

```bash
terraform version
```

### AWS credentials

An AWS account/user with permission to create IAM roles, IAM OIDC providers, and inline policies.

```bash
aws configure
```

You'll be prompted for:
- **AWS Access Key ID**: AWS Console → IAM → Your user → Security credentials → Create access key
- **AWS Secret Access Key**: shown once when creating the key, copy it immediately
- **Default region**: `us-east-1` (the only region this stack's `region` variable accepts)
- **Default output format**: press Enter to skip

Or export credentials directly:

```bash
export AWS_ACCESS_KEY_ID="your-access-key-id"
export AWS_SECRET_ACCESS_KEY="your-secret-access-key"
export AWS_DEFAULT_REGION="us-east-1"
```

### Amazon Bedrock model access

In the AWS Console: **Amazon Bedrock → Model access → Manage model access** → enable Amazon Nova Pro, Qwen3 Coder Next, Kimi K2.5, and DeepSeek V3.2 → **Save changes** → wait for **Access granted**.

### GitHub Personal Access Token

Required only when `manage_github = true`. Lets Terraform write the `AWS_ROLE_ARN` secret and create the review labels.

1. **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**
2. **Resource owner**: your account or organization
3. **Repository access**: Only select repositories → choose your target repository
4. **Permissions**:
   - **Administration**: Read and write
   - **Secrets**: Read and write
   - **Actions**: Read-only
   - **Metadata**: Read-only (required, auto-enabled)
5. Generate and copy the token (shown once)

```bash
export GITHUB_TOKEN="github_pat_your_token_here"
```

## Target repository

If you're applying this against your **own** repository change these:

```bash
gh api /users/<your-username> --jq .id     # or /orgs/<org> --jq .id for an org (this is github_owner_id)
gh api /repos/<owner>/<repo> --jq .id      # github_repository_id
```

## Execution guide

**1.** From the repo root:

```bash
cd infra
```

**2.** Create a local variables file:

```bash
cp terraform.tfvars.example local.auto.tfvars
```

**3.** Edit `local.auto.tfvars`. Every field is optional if you're applying against `IkkaLabs/praas` as-is; set them to target your own repository instead:

```hcl
github_owner         = "<owner>"
github_repository    = "<repo>"
github_owner_id      = <numeric-owner-id>
github_repository_id = <numeric-repo-id>
manage_github        = true

# Optional: reuse an existing IAM OIDC provider already registered in this AWS account
# github_oidc_provider_arn = "arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com"
```

**4.** Export your GitHub token (only needed when `manage_github = true`):

```bash
export GITHUB_TOKEN="github_pat_your_token_here"
```

**5.**

```bash
terraform init
```

**6.**

```bash
terraform validate
```

**7.** Review before applying:

```bash
terraform plan
```

**8.**

```bash
terraform apply
```

Provisions the IAM OIDC provider, IAM role, and Bedrock permissions. When `manage_github = true`, it also creates the `AWS_ROLE_ARN` secret and all `praas-*` labels.

**9.** If `manage_github = false` (or the secret step didn't run, see below), set the secret manually: **GitHub repo → Settings → Secrets and variables → Actions → New repository secret**, name `AWS_ROLE_ARN`, value:

```bash
terraform output -raw github_actions_role_arn
```

### Applying the GitHub side without AWS credentials

The `AWS_ROLE_ARN` secret resource (`github_actions_secret.aws_role_arn`) reads its value from the IAM role's ARN, so it can't be created until the AWS side applies successfully. The review labels (`github_issue_label.review`) don't depend on AWS at all. If you want those live before your AWS credentials are sorted out, target them directly:

```bash
terraform apply -target='github_issue_label.review'
```

Terraform will warn that targeted applies aren't for routine use; that's expected here. Run a full `terraform apply` (no `-target`) once your AWS credentials work, to pick up the IAM/OIDC/Bedrock resources and the secret.

## How to tear it down

```bash
terraform destroy
```

> [!WARNING]
> `terraform destroy` only removes the GitHub OIDC Identity Provider in AWS if this workspace created it. If other roles in your account share that provider, set `github_oidc_provider_arn` in your variables so Terraform treats it as external and doesn't delete a shared resource.

## Managing any GitHub repository with Terraform and a PAT

The `manage_github` path above uses the [`integrations/github`](https://registry.terraform.io/providers/integrations/github/latest) provider to manage GitHub itself (repositories, Actions secrets, labels, branch protection), authenticated with a personal access token (PAT). This section explains the underlying mechanism generically, for adapting it beyond `manage_github`'s built-in scope.

### 1. Create a PAT

GitHub → Settings → Developer settings → Personal access tokens.

- **Fine-grained (preferred):** scope to the specific repositories/organization, grant only what's needed:
  - `Administration`: create repositories, manage settings, labels, branch protection.
  - `Secrets` and `Actions`: read and write Actions secrets and variables.
  - `Contents`: manage files, branches, releases.
  - `Metadata`: read (required by the provider).
- **Classic:** `repo` scope covers most repository management; add `admin:org` for organization resources and `workflow` to edit workflow files.

### 2. Provide the token by environment variable

```bash
export GITHUB_TOKEN="github_pat_xxx"
```

Never hard-code it in `.tf` or `.tfvars` files.

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

provider "github" {
  owner = "IkkaLabs"
}
```

### 4. Declare resources

```hcl
resource "github_repository" "example" {
  name       = "example-repo"
  visibility = "private"
}

resource "github_actions_secret" "aws_role_arn" {
  repository      = github_repository.example.name
  secret_name     = "AWS_ROLE_ARN"
  plaintext_value = var.aws_role_arn # mark the variable sensitive
}

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

Don't declare a `github_repository` for a repo you didn't create with this stack. Import it instead, so Terraform adopts it rather than trying to create a duplicate:

```bash
terraform import github_repository.example example-repo
terraform import 'github_issue_label.review' example-repo:needs-review
```

### Cautions

- **State holds secrets in plaintext.** Any `plaintext_value` and the PAT's effects live in `terraform.tfstate`. Use a protected remote backend (e.g. S3 with a lock table); never commit state. `.gitignore` here already excludes `*.tfstate*` and `*.tfvars`.
- **A PAT is a long-lived credential.** Scope it tightly, rotate it, and store it in a secret manager, not shell history. Where the target supports OIDC (as this stack's AWS side does), prefer short-lived OIDC federation over a standing PAT.
- **`owner` matters.** For an organization, `owner` must be the org login and the PAT must have organization access, or resources get created under the token owner's personal account instead.
