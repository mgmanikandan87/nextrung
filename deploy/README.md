# Deploying NextRung

## Pipeline (recommended)

`.github/workflows/ci.yml` runs on every push and pull request: validate data (`scripts/validate.py`), build (`scripts/build.py`), browser smoke test (`scripts/smoke.js`, mocked backend), Lambda import check. On `main` it then deploys with `deploy/deploy.sh` (Lambda code, API routes, Cognito, Amplify) using an OIDC role, and runs post-deploy checks (site 200, `/health` ok, site points at the API, `/me` without a token is 401). Pull requests never deploy.

One-time setup:
1. Create the GitHub repo and push this code.
2. In AWS CloudShell: `REPO=owner/name bash deploy/aws-github-oidc.sh` (creates the OIDC provider and the `nextrung-github-deploy` role trusted by that repo's `main` only, in both the classic and GitHub's newer immutable-subject form; prints the role ARN). Re-run it if a deploy fails with "Not authorized to perform sts:AssumeRoleWithWebIdentity".
3. GitHub → Settings → Secrets and variables → Actions: `AWS_DEPLOY_ROLE_ARN` (from step 2). The export key is kept in SSM Parameter Store (`/nextrung/export_key`), so CI deploys keep the same export URL.
4. Optional, for the quarterly refresh agent (`.github/workflows/refresh.yml`): `ANTHROPIC_API_KEY`. It runs 1 Jan/Apr/Jul/Oct and on demand (Actions → quarterly-data-refresh → Run workflow), opens a PR with refreshed data and a `review/refresh-<date>.md`; merging deploys.

Settings that live in SSM Parameter Store (read by `deploy.sh`, so CI never needs them as secrets):
- `/nextrung/export_key` (SecureString): the export/digest key.
- `/nextrung/admin_emails` (String, optional): comma-separated emails that get the admin role on sign-in. `aws ssm put-parameter --name /nextrung/admin_emails --type String --overwrite --value "you@example.com"`, then redeploy.
- `/nextrung/ses_from` (String, optional): a verified SES sender (`aws ses verify-email-identity --email-address you@example.com`, click the link; request production access to email unverified recipients). With it set, mentor notes, reviews, assignment requests and the weekly digest are emailed; without it, everything still works in-app.

Rotate the export key: `aws ssm put-parameter --name /nextrung/export_key --type SecureString --overwrite --value <new>` then redeploy; the old URL stops working.

## Manual (CloudShell)

Bundle `deploy/deploy.sh`, `deploy/lambda/handler.py`, `deploy/lambda/checks.json` (from `scripts/build.py`), and `dist/index.html` as `deploy/site/index.html`; upload to CloudShell; `bash deploy.sh`. The script is idempotent. CloudShell's bundled CLI can lag; the script prefers `~/bin/aws` if present (official v2 installer into `~/awscli`, binary in `~/bin`).

Costs: Amplify Hosting, API Gateway, Lambda, DynamoDB and Cognito (Essentials, 10k MAU free) are pay-per-use and sit inside the free tier at test scale. GitHub Actions: free for public repos; 2,000 minutes/month on private.

Cleanup: `aws amplify delete-app --app-id <id>`, `aws apigatewayv2 delete-api --api-id <id>`, `aws lambda delete-function --function-name nextrung-api`, `aws dynamodb delete-table --table-name nextrung` (and `nextrung-responses`), `aws cognito-idp delete-user-pool --user-pool-id <id>`, then the two IAM roles `nextrung-lambda` and `nextrung-github-deploy`.
