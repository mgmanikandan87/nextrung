#!/usr/bin/env bash
# One-time: let GitHub Actions deploy NextRung to this AWS account without stored keys (OIDC).
# Run in AWS CloudShell:  REPO=owner/name bash aws-github-oidc.sh
# Prints the role ARN to put in the GitHub secret AWS_DEPLOY_ROLE_ARN.
set -euo pipefail
REPO="${REPO:?set REPO=owner/name}"
REGION="${REGION:-ap-south-1}"; NAME="${NAME:-nextrung}"; ROLE="${ROLE:-nextrung-github-deploy}"
export AWS_DEFAULT_REGION="$REGION" AWS_PAGER=""
[ -x "$HOME/bin/aws" ] && export PATH="$HOME/bin:$PATH"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

# 1. OIDC identity provider for GitHub (idempotent)
PROV="arn:aws:iam::$ACCOUNT:oidc-provider/token.actions.githubusercontent.com"
if ! aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$PROV" >/dev/null 2>&1; then
  aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 >/dev/null
  echo "created OIDC provider"
fi

# 2. Role trusted only by this repo's main branch (and PRs cannot assume it).
#    GitHub now issues an "immutable subject" by default: repo:<owner>@<owner_id>/<repo>@<repo_id>:ref:...
#    so we trust both the classic and the immutable form (ids from the public GitHub API).
IDS=$(curl -sS "https://api.github.com/repos/$REPO")
OWNER_ID=$(echo "$IDS" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["owner"]["id"])')
REPO_ID=$(echo "$IDS" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["id"])')
IMM="repo:${REPO%%/*}@$OWNER_ID/${REPO##*/}@$REPO_ID"
TRUST=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Federated":"$PROV"},"Action":"sts:AssumeRoleWithWebIdentity",
 "Condition":{"StringEquals":{"token.actions.githubusercontent.com:aud":"sts.amazonaws.com"},
              "StringLike":{"token.actions.githubusercontent.com:sub":["repo:$REPO:ref:refs/heads/main","$IMM:ref:refs/heads/main"]}}}]}
EOF
)
if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$ROLE" --policy-document "$TRUST"
else
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document "$TRUST" --max-session-duration 3600 >/dev/null
fi

# 3. Permissions: exactly what deploy.sh touches, scoped to the nextrung resources where the service allows it
aws iam put-role-policy --role-name "$ROLE" --policy-name nextrung-deploy --policy-document "$(cat <<EOF
{"Version":"2012-10-17","Statement":[
 {"Sid":"Read","Effect":"Allow","Action":["sts:GetCallerIdentity","amplify:ListApps","apigatewayv2:GET","apigateway:GET","cognito-idp:ListUserPools","cognito-idp:ListUserPoolClients","lambda:GetFunction","lambda:GetFunctionConfiguration","lambda:GetPolicy","iam:GetRole","dynamodb:DescribeTable"],"Resource":"*"},
 {"Sid":"Dynamo","Effect":"Allow","Action":["dynamodb:CreateTable","dynamodb:DescribeTable","dynamodb:UpdateTable"],"Resource":["arn:aws:dynamodb:$REGION:$ACCOUNT:table/$NAME","arn:aws:dynamodb:$REGION:$ACCOUNT:table/$NAME-responses"]},
 {"Sid":"Cognito","Effect":"Allow","Action":["cognito-idp:CreateUserPool","cognito-idp:CreateUserPoolClient","cognito-idp:DescribeUserPool","cognito-idp:UpdateUserPool"],"Resource":"*"},
 {"Sid":"LambdaRole","Effect":"Allow","Action":["iam:CreateRole","iam:AttachRolePolicy","iam:PutRolePolicy","iam:PassRole","iam:GetRolePolicy"],"Resource":"arn:aws:iam::$ACCOUNT:role/$NAME-lambda"},
 {"Sid":"Lambda","Effect":"Allow","Action":["lambda:CreateFunction","lambda:UpdateFunctionCode","lambda:UpdateFunctionConfiguration","lambda:AddPermission","lambda:RemovePermission","lambda:GetFunction","lambda:TagResource"],"Resource":"arn:aws:lambda:$REGION:$ACCOUNT:function:$NAME-api"},
 {"Sid":"ApiGw","Effect":"Allow","Action":["apigateway:POST","apigateway:PUT","apigateway:PATCH","apigateway:GET","apigateway:DELETE"],"Resource":"arn:aws:apigateway:$REGION::/apis*"},
 {"Sid":"Ssm","Effect":"Allow","Action":["ssm:GetParameter","ssm:PutParameter"],"Resource":"arn:aws:ssm:$REGION:$ACCOUNT:parameter/$NAME/*"},
 {"Sid":"Amplify","Effect":"Allow","Action":["amplify:CreateApp","amplify:UpdateApp","amplify:GetApp","amplify:GetBranch","amplify:CreateBranch","amplify:CreateDeployment","amplify:StartDeployment","amplify:GetJob","amplify:ListJobs"],"Resource":"*"}
]}
EOF
)"
ARN=$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)
echo; echo "================ DONE ================"
echo "GitHub secret AWS_DEPLOY_ROLE_ARN = $ARN"
echo "Export key lives in SSM parameter /$NAME/export_key (no GitHub secret needed)."
echo "Trusted: repo:$REPO and $IMM, branch main only."
