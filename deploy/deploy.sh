#!/usr/bin/env bash
# Disha for Engineers: one-shot deploy from AWS CloudShell (or any shell with AWS CLI v2 + jq).
#   Site      -> AWS Amplify Hosting (manual deploy, https, free tier)
#   Responses -> API Gateway (HTTP API) + Lambda + DynamoDB (pay per request, free tier)
# Idempotent: safe to re-run; re-running redeploys the site and updates the Lambda code.
set -euo pipefail

REGION="${REGION:-ap-south-1}"
APP_NAME="${APP_NAME:-disha-for-engineers}"
BRANCH="main"
FN="${FN:-disha-responses}"
TABLE="${TABLE:-disha-responses}"
ROLE="${ROLE:-disha-responses-lambda}"
HERE="$(cd "$(dirname "$0")" && pwd)"
export AWS_DEFAULT_REGION="$REGION" AWS_PAGER=""

need() { command -v "$1" >/dev/null || { echo "missing: $1"; exit 1; }; }
need aws; need jq; need zip; need curl
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
echo "account $ACCOUNT, region $REGION"

# ---------- 1. DynamoDB ----------
if ! aws dynamodb describe-table --table-name "$TABLE" >/dev/null 2>&1; then
  aws dynamodb create-table --table-name "$TABLE" --billing-mode PAY_PER_REQUEST \
    --attribute-definitions AttributeName=id,AttributeType=S --key-schema AttributeName=id,KeyType=HASH >/dev/null
  aws dynamodb wait table-exists --table-name "$TABLE"
fi
echo "table $TABLE ready"

# ---------- 2. IAM role ----------
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  aws iam put-role-policy --role-name "$ROLE" --policy-name ddb --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"dynamodb:PutItem\",\"dynamodb:Scan\"],\"Resource\":\"arn:aws:dynamodb:$REGION:$ACCOUNT:table/$TABLE\"}]}"
  echo "waiting for IAM to propagate"; sleep 12
fi
ROLE_ARN=$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)

# ---------- 3. Lambda + function URL ----------
EXPORT_KEY_FILE="$HERE/.export_key"
if [ -f "$EXPORT_KEY_FILE" ]; then EXPORT_KEY=$(cat "$EXPORT_KEY_FILE"); else EXPORT_KEY=$(head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32); echo -n "$EXPORT_KEY" > "$EXPORT_KEY_FILE"; fi
( cd "$HERE/lambda" && rm -f ../fn.zip && zip -q ../fn.zip handler.py )
if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --zip-file "fileb://$HERE/fn.zip" >/dev/null
  aws lambda wait function-updated --function-name "$FN"
  aws lambda update-function-configuration --function-name "$FN" --environment "Variables={TABLE=$TABLE,EXPORT_KEY=$EXPORT_KEY}" >/dev/null
else
  for i in 1 2 3 4 5 6; do
    if aws lambda create-function --function-name "$FN" --runtime python3.12 --handler handler.handler --role "$ROLE_ARN" \
        --zip-file "fileb://$HERE/fn.zip" --timeout 15 --memory-size 256 \
        --environment "Variables={TABLE=$TABLE,EXPORT_KEY=$EXPORT_KEY}" >/dev/null 2>/tmp/lambda.err; then break; fi
    echo "create-function retry $i ($(head -c 120 /tmp/lambda.err))"; sleep 8
  done
fi
aws lambda wait function-active --function-name "$FN"
# API Gateway HTTP API in front of the Lambda. (A Lambda function URL with auth NONE returned 403 in this
# account even with the correct resource policy; API Gateway worked first time, so it is the endpoint.)
FN_ARN=$(aws lambda get-function --function-name "$FN" --query Configuration.FunctionArn --output text)
API_ID=$(aws apigatewayv2 get-apis --query "Items[?Name=='$FN'].ApiId | [0]" --output text)
if [ -z "$API_ID" ] || [ "$API_ID" = "None" ]; then
  API_ID=$(aws apigatewayv2 create-api --name "$FN" --protocol-type HTTP --target "$FN_ARN" \
    --cors-configuration "AllowOrigins=*,AllowMethods=GET,POST,OPTIONS,AllowHeaders=content-type" --query ApiId --output text)
  aws lambda add-permission --function-name "$FN" --statement-id "apigw-$API_ID" --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com --source-arn "arn:aws:execute-api:$REGION:$ACCOUNT:$API_ID/*" >/dev/null
  sleep 3
fi
ENDPOINT="$(aws apigatewayv2 get-api --api-id "$API_ID" --query ApiEndpoint --output text)/"
echo "responses endpoint $ENDPOINT"
curl -s -o /dev/null -w "health check: HTTP %{http_code}\n" "${ENDPOINT}health"

# ---------- 4. Site: inject endpoint, zip, deploy to Amplify ----------
mkdir -p "$HERE/build"
sed "s#__ENDPOINT__#${ENDPOINT}#g" "$HERE/site/index.html" > "$HERE/build/index.html"
( cd "$HERE/build" && rm -f ../site.zip && zip -q ../site.zip index.html )
APP_ID=$(aws amplify list-apps --query "apps[?name=='$APP_NAME'].appId | [0]" --output text)
if [ -z "$APP_ID" ] || [ "$APP_ID" = "None" ]; then
  APP_ID=$(aws amplify create-app --name "$APP_NAME" --platform WEB --query app.appId --output text)
fi
if ! aws amplify get-branch --app-id "$APP_ID" --branch-name "$BRANCH" >/dev/null 2>&1; then
  aws amplify create-branch --app-id "$APP_ID" --branch-name "$BRANCH" --stage PRODUCTION >/dev/null
fi
DEP=$(aws amplify create-deployment --app-id "$APP_ID" --branch-name "$BRANCH")
JOB_ID=$(echo "$DEP" | jq -r .jobId); UPLOAD=$(echo "$DEP" | jq -r .zipUploadUrl)
curl -sS -T "$HERE/site.zip" "$UPLOAD" -H "Content-Type: application/zip"
aws amplify start-deployment --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$JOB_ID" >/dev/null
for i in $(seq 1 40); do
  ST=$(aws amplify get-job --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$JOB_ID" --query job.summary.status --output text)
  [ "$ST" = "SUCCEED" ] && break; [ "$ST" = "FAILED" ] && { echo "amplify deploy failed"; exit 1; }; sleep 5
done
SITE_URL="https://$BRANCH.$APP_ID.amplifyapp.com"

# ---------- 5. Report ----------
cat > "$HERE/outputs.json" <<EOF
{"site_url":"$SITE_URL","responses_endpoint":"$ENDPOINT","export_json":"${ENDPOINT}?key=$EXPORT_KEY","export_csv":"${ENDPOINT}?key=$EXPORT_KEY&format=csv","health":"${ENDPOINT}health","region":"$REGION","app_id":"$APP_ID","api_id":"$API_ID","table":"$TABLE"}
EOF
echo; echo "================ DONE ================"
echo "Site:            $SITE_URL"
echo "Responses API:   $ENDPOINT"
echo "Health:          ${ENDPOINT}health"
echo "Export (JSON):   ${ENDPOINT}?key=$EXPORT_KEY"
echo "Export (CSV):    ${ENDPOINT}?key=$EXPORT_KEY&format=csv"
echo "Keep the export key private. Saved in deploy/outputs.json and deploy/.export_key."
