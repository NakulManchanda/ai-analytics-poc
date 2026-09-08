data "aws_iam_policy_document" "github_actions_oidc_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Environment protection gates this token; the release workflow separately
    # verifies that checkout is an exact v* tag before it can plan or apply.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:NakulManchanda/ai-analytics-poc:environment:terraform-production"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      values   = ["NakulManchanda/ai-analytics-poc/.github/workflows/terraform-release.yml@refs/heads/main"]
    }
  }
}

# The role policy is intentionally supplied outside this public repository.
# Bootstrap grants it only the reviewed actions/resources needed by this POC;
# it must never be AdministratorAccess or contain static credentials.
resource "aws_iam_role" "github_actions_terraform" {
  name               = "${local.name}-github-actions-terraform"
  assume_role_policy = data.aws_iam_policy_document.github_actions_oidc_trust.json
}
