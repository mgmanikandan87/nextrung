# Deploying NextRung

1. `python3 scripts/build.py` at the repo root.
2. Bundle: `deploy/deploy.sh`, `deploy/lambda/handler.py`, and `dist/index.html` copied to `deploy/site/index.html`.
3. Upload the bundle to AWS CloudShell (Actions → Upload file), `unzip`, `bash deploy.sh`.
4. The script prints the site URL, the responses endpoint, and the export URLs; it also writes `outputs.json` and `.export_key` next to itself. Keep the key private.

Costs: Amplify Hosting, API Gateway, Lambda and DynamoDB are all pay-per-use and sit inside the free tier at test scale.

CloudShell note: the bundled AWS CLI can lag; the script prefers `~/bin/aws` if present (install with the official v2 installer into `~/awscli`, binary in `~/bin`).

Cleanup: `aws amplify delete-app --app-id <id>`, `aws apigatewayv2 delete-api --api-id <id>`, `aws lambda delete-function --function-name nextrung-api`, `aws dynamodb delete-table --table-name nextrung-api`, `aws iam delete-role-policy --role-name nextrung-api-lambda --policy-name ddb && aws iam detach-role-policy --role-name nextrung-api-lambda --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole && aws iam delete-role --role-name nextrung-api-lambda`.
