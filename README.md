# praas

praas is an AI-powered pull request review agent that works in your own GitHub repository. This repository contains the agent itself, in [`praas/`](praas/) — containerized and executed directly within GitHub Actions review workflows, with integrations for AWS Bedrock and other AI providers.

## Pull Request Review Workflows

Reviews are triggered by adding a label to a GitHub pull request. Each label routes the review to a specific model backend:

| Label | Target Model | Provider |
| --- | --- | --- |
| `praas-gemini` | Gemini 2.5 Flash | Google Gemini API |
| `praas-bedrock-nova` | Amazon Nova Pro | Amazon Bedrock (`us-east-1`) |
| `praas-bedrock-qwen` | Qwen3 Coder Next | Amazon Bedrock (`us-east-1`) |
| `praas-bedrock-kimi-k2.5` | Kimi K2.5 | Amazon Bedrock (`us-east-1`) |
| `praas-bedrock-deepseek` | DeepSeek V3.2 | Amazon Bedrock (`us-east-1`) |
| `praas-local-qwen35-9b` | Qwen3.5 9B | Local / OpenAI-compatible endpoint |
| `praas-local-gemma4-12b` | Gemma 4 12B | Local / OpenAI-compatible endpoint |
| `praas-all` | Gemini + all Bedrock models | Parallel execution |

## Architecture Overview

```
.
├── .github/workflows/        # Label-triggered GitHub Actions review workflows
├── Dockerfile                 # Container image build spec for praas
├── infra/                    # Terraform code for AWS OIDC trust and GitHub resources
└── praas/                    # praas source code and configuration
```

praas analyzes pull request diffs, provides specialized multi-lens feedback (correctness, security, testing, documentation), and posts reviews directly back to GitHub pull requests.

## Required Secrets

Set these in your GitHub repository under **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | When required | How to get it |
| --- | --- | --- |
| `GEMINI_API_KEY` | Google Gemini labels | [Google AI Studio](https://aistudio.google.com/app/apikey) → Create API key |
| `AWS_ROLE_ARN` | Any `praas-bedrock-*` label | Output from `terraform apply` in [`infra/`](infra/). See [setup-praas.md](setup-praas.md) |
| `LOCAL_LLM_BASE` | Any `praas-local-*` label | Base URL of your OpenAI-compatible endpoint (e.g. `http://localhost:11434/v1`) |
| `LOCAL_LLM_KEY` | Any `praas-local-*` label | API key or auth token for the local endpoint |

## Setup Guide

Follow these steps to integrate praas into your own repository.

### Step 1: Install Prerequisites

**Terraform** (version 1.6 or newer): [Install Terraform](https://developer.hashicorp.com/terraform/install)

Verify:
```bash
terraform version
```

**GitHub CLI**: [Install gh](https://cli.github.com/)

Authenticate:
```bash
gh auth login
```

**AWS CLI**: [Install AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)

Configure with your credentials:
```bash
aws configure
```

You will be prompted for:
- **AWS Access Key ID**: Found in AWS Console → IAM → Your user → Security credentials → Create access key
- **AWS Secret Access Key**: Shown once when you create the access key — copy it immediately
- **Default region**: Enter `us-east-1`
- **Default output format**: Press Enter to skip

### Step 2: Enable Bedrock Model Access

In the AWS Console:

1. Go to **Amazon Bedrock → Model access → Manage model access**
2. Enable all of: Amazon Nova Pro, Qwen3 Coder Next, Kimi K2.5, DeepSeek V3.2
3. Click **Save changes** and wait for status to become **Access granted**

### Step 3: Create a GitHub Personal Access Token

This allows Terraform to write the `AWS_ROLE_ARN` secret and create review labels in your repository automatically.

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

Export the token in your terminal:
```bash
export GITHUB_TOKEN="github_pat_your_token_here"
```

### Step 4: Find Your Repository Identifiers

You need four values to configure Terraform. Run these commands (replace `<owner>` and `<repo>` with your GitHub username or organization name and repository name):

**github_owner**: Your GitHub username or organization name. Example: `octocat`

**github_repository**: The repository name. Example: `my-project`

**github_owner_id** — run one of the following depending on whether the owner is a user or organization:
```bash
gh api /users/<owner> --jq .id
```
```bash
gh api /orgs/<owner> --jq .id
```

**github_repository_id**:
```bash
gh api /repos/<owner>/<repo> --jq .id
```

### Step 5: Provision Infrastructure

Navigate to the `infra/` directory and create your variables file:

```bash
cd infra
```

```bash
cp terraform.tfvars.example local.auto.tfvars
```

Open `local.auto.tfvars` and fill in the values from Step 4:

```hcl
github_owner         = "<owner>"
github_repository    = "<repo>"
github_owner_id      = <numeric-owner-id>
github_repository_id = <numeric-repo-id>
manage_github        = true
```

Run each Terraform command in sequence:

```bash
terraform init
```
Downloads the required provider plugins.

```bash
terraform validate
```
Checks configuration for syntax errors.

```bash
terraform plan
```
Previews what resources will be created — review this before applying.

```bash
terraform apply
```
Creates the IAM role, OIDC provider, Bedrock permissions, GitHub secret, and review labels.

### Step 6: Set Additional Secrets (Optional)

If you plan to use Google Gemini or a local model, set the remaining secrets in **GitHub → Settings → Secrets and variables → Actions → New repository secret**:

- `GEMINI_API_KEY`: Your Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey)
- `LOCAL_LLM_BASE`: The base URL of your local endpoint
- `LOCAL_LLM_KEY`: The API key for your local endpoint

### Step 7: Copy Workflow and Agent Files

Clone this repository, copy the four required paths into your own repository, then commit and push.

Clone this repository into a temporary folder:

```bash
git clone https://github.com/IkkaLabs/praas.git praas-src
```

Copy the workflows, Dockerfile, agent, and infra code into your repository (replace `your-repo` with the path to your local repository clone):

```bash
cp -R praas-src/.github praas-src/Dockerfile praas-src/praas praas-src/infra your-repo/
```

Commit and push from your repository:

```bash
cd your-repo
```
```bash
git add .github Dockerfile praas infra
```
```bash
git commit -m "add praas review agent"
```
```bash
git push
```

### Step 8: Verify the Integration

1. Create a pull request in your repository
2. Add the label `praas-bedrock-nova` to the pull request
3. Go to **Actions** and watch the triggered workflow run
4. Confirm the **Configure temporary AWS credentials** step succeeds and praas posts a review comment to your pull request

## Documentation

- [`praas/README.md`](praas/README.md): CLI options, supported git providers, configuration parameters, and unit testing guidelines
- [`setup-praas.md`](setup-praas.md): Terraform variables, IAM policy structure, and teardown instructions
