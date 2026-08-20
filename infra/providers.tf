terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = var.tags
  }
}

# Only configured when manage_github is true. Supply the PAT through the
# GITHUB_TOKEN environment variable; never hard-code it here or in a .tfvars
# file. The token needs repository administration, secrets, and actions
# permissions on var.github_owner/var.github_repository.
provider "github" {
  owner = var.github_owner
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
