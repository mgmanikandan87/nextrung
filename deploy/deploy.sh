#!/usr/bin/env bash
# NextRung: one-shot deploy from AWS CloudShell (AWS CLI v2 + jq + zip + curl).
#   Site      -> AWS Amplify Hosting (manual deploy, https)
#   API       -> API Gateway HTTP API -> Lambda (python3.12) -> DynamoDB
#   Accounts  -> Cognito user pool, passwordless email OTP (Essentials tier)
# Idempotent: safe to re-run; re-runs update Lambda code, routes and push a new site build.
set -euo pipefail

REGION="${REGION:-ap-south-1}"
NAME="${NAME:-nextrung}"
AMPLIFY_APP_NAME="${AMPLIFY_APP_NAME:-disha-for-engineers}"   # existing Amplify app keeps its URL; renamed below
BRANCH="main"
HERE="$(cd "$(dirname "$0")" && pwd)"
export AWS_DEFAULT_REGION="$REGION" AWS_PAGER=""
[ -x "$HOME/bin/aws" ] && export PATH="$HOME/bin:$PATH"   # CloudShell's bundled CLI can lag; a newer one installed in ~/bin wins
need() { command -v "$1" >/dev/null || { echo "missing: $1"; exit 1; }; }
need aws; need jq; need zip; need curl
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
echo "account $ACCOUNT, region $REGION, name $NAME"

# ---------- 1. DynamoDB ----------
for tbl in "$NAME" "$NAME-responses"; do
  if ! aws dynamodb describe-table --table-name "$tbl" >/dev/null 2>&1; then
    if [ "$tbl" = "$NAME" ]; then
      aws dynamodb create-table --table-name "$tbl" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=sk,AttributeType=S \
        --key-schema AttributeName=pk,KeyType=HASH AttributeName=sk,KeyType=RANGE >/dev/null
    else
      aws dynamodb create-table --table-name "$tbl" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions AttributeName=id,AttributeType=S --key-schema AttributeName=id,KeyType=HASH >/dev/null
    fi
    aws dynamodb wait table-exists --table-name "$tbl"
  fi
done
echo "tables ready"

# ---------- 2. Cognito (passwordless email OTP) ----------
POOL_ID=$(aws cognito-idp list-user-pools --max-results 60 --query "UserPools[?Name=='$NAME'].Id | [0]" --output text)
if [ -z "$POOL_ID" ] || [ "$POOL_ID" = "None" ]; then
  POOL_ID=$(aws cognito-idp create-user-pool --pool-name "$NAME" --user-pool-tier ESSENTIALS \
    --policies '{"SignInPolicy":{"AllowedFirstAuthFactors":["PASSWORD","EMAIL_OTP"]}}' \
    --username-attributes email --auto-verified-attributes email \
    --username-configuration CaseSensitive=false \
    --admin-create-user-config AllowAdminCreateUserOnly=false \
    --email-configuration EmailSendingAccount=COGNITO_DEFAULT \
    --deletion-protection INACTIVE --query UserPool.Id --output text)
fi
CLIENT_ID=$(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" --max-results 10 --query "UserPoolClients[?ClientName=='web'].ClientId | [0]" --output text)
if [ -z "$CLIENT_ID" ] || [ "$CLIENT_ID" = "None" ]; then
  CLIENT_ID=$(aws cognito-idp create-user-pool-client --user-pool-id "$POOL_ID" --client-name web --no-generate-secret \
    --explicit-auth-flows ALLOW_USER_AUTH ALLOW_REFRESH_TOKEN_AUTH \
    --id-token-validity 24 --access-token-validity 24 --refresh-token-validity 30 \
    --token-validity-units IdToken=hours,AccessToken=hours,RefreshToken=days \
    --query UserPoolClient.ClientId --output text)
fi
echo "cognito pool $POOL_ID client $CLIENT_ID"

# ---------- 3. IAM role ----------
ROLE="$NAME-lambda"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  NEWROLE=1
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name app --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
 {\"Effect\":\"Allow\",\"Action\":[\"dynamodb:PutItem\",\"dynamodb:GetItem\",\"dynamodb:UpdateItem\",\"dynamodb:DeleteItem\",\"dynamodb:Query\",\"dynamodb:Scan\",\"dynamodb:BatchWriteItem\"],\"Resource\":[\"arn:aws:dynamodb:$REGION:$ACCOUNT:table/$NAME\",\"arn:aws:dynamodb:$REGION:$ACCOUNT:table/$NAME-responses\"]},
 {\"Effect\":\"Allow\",\"Action\":[\"cognito-idp:AdminGetUser\",\"cognito-idp:AdminCreateUser\",\"cognito-idp:AdminInitiateAuth\",\"cognito-idp:AdminRespondToAuthChallenge\",\"cognito-idp:AdminSetUserPassword\",\"cognito-idp:AdminDeleteUser\"],\"Resource\":\"arn:aws:cognito-idp:$REGION:$ACCOUNT:userpool/$POOL_ID\"}]}"
[ "${NEWROLE:-}" = "1" ] && { echo "waiting for IAM to propagate"; sleep 12; }
ROLE_ARN=$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)

# ---------- 4. Lambda ----------
FN="$NAME-api"
# Export key: env EXPORT_KEY > SSM parameter /nextrung/export_key > local .export_key > generate. Stored in SSM so CI runs keep the same key.
EXPORT_KEY_FILE="$HERE/.export_key"; PARAM="/$NAME/export_key"
if [ -z "${EXPORT_KEY:-}" ]; then EXPORT_KEY=$(aws ssm get-parameter --name "$PARAM" --with-decryption --query Parameter.Value --output text 2>/dev/null || true); fi
if [ -z "${EXPORT_KEY:-}" ] || [ "$EXPORT_KEY" = "None" ]; then
  if [ -f "$EXPORT_KEY_FILE" ]; then EXPORT_KEY=$(cat "$EXPORT_KEY_FILE"); else EXPORT_KEY=$(head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32); fi
fi
aws ssm put-parameter --name "$PARAM" --type SecureString --value "$EXPORT_KEY" --overwrite >/dev/null 2>&1 || echo "note: could not store export key in SSM (no permission?); using local/env key"
[ -z "${CI:-}" ] && echo -n "$EXPORT_KEY" > "$EXPORT_KEY_FILE"
ENV="Variables={TABLE=$NAME,RESP_TABLE=$NAME-responses,EXPORT_KEY=$EXPORT_KEY,POOL_ID=$POOL_ID,CLIENT_ID=$CLIENT_ID}"
( cd "$HERE/lambda" && rm -f ../fn.zip && zip -q ../fn.zip handler.py )
if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --zip-file "fileb://$HERE/fn.zip" >/dev/null
  aws lambda wait function-updated --function-name "$FN"
  aws lambda update-function-configuration --function-name "$FN" --environment "$ENV" --timeout 20 >/dev/null
  aws lambda wait function-updated --function-name "$FN"
else
  for i in 1 2 3 4 5 6; do
    if aws lambda create-function --function-name "$FN" --runtime python3.12 --handler handler.handler --role "$ROLE_ARN" \
        --zip-file "fileb://$HERE/fn.zip" --timeout 20 --memory-size 256 --environment "$ENV" >/dev/null 2>/tmp/lambda.err; then break; fi
    echo "create-function retry $i ($(head -c 120 /tmp/lambda.err))"; sleep 8
  done
  aws lambda wait function-active --function-name "$FN"
fi
FN_ARN=$(aws lambda get-function --function-name "$FN" --query Configuration.FunctionArn --output text)

# ---------- 5. API Gateway HTTP API + JWT authorizer ----------
API_ID=$(aws apigatewayv2 get-apis --query "Items[?Name=='$NAME'].ApiId | [0]" --output text)
if [ -z "$API_ID" ] || [ "$API_ID" = "None" ]; then
  API_ID=$(aws apigatewayv2 create-api --name "$NAME" --protocol-type HTTP --target "$FN_ARN" \
    --cors-configuration "AllowOrigins=*,AllowMethods=GET,POST,PUT,DELETE,OPTIONS,AllowHeaders=content-type,authorization" --query ApiId --output text)
  aws lambda add-permission --function-name "$FN" --statement-id "apigw-$API_ID" --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com --source-arn "arn:aws:execute-api:$REGION:$ACCOUNT:$API_ID/*" >/dev/null
fi
INT_ID=$(aws apigatewayv2 get-integrations --api-id "$API_ID" --query "Items[0].IntegrationId" --output text)
AUTH_ID=$(aws apigatewayv2 get-authorizers --api-id "$API_ID" --query "Items[?Name=='cognito'].AuthorizerId | [0]" --output text)
if [ -z "$AUTH_ID" ] || [ "$AUTH_ID" = "None" ]; then
  AUTH_ID=$(aws apigatewayv2 create-authorizer --api-id "$API_ID" --authorizer-type JWT --name cognito \
    --identity-source '$request.header.Authorization' \
    --jwt-configuration "Audience=$CLIENT_ID,Issuer=https://cognito-idp.$REGION.amazonaws.com/$POOL_ID" --query AuthorizerId --output text)
fi
for rk in "GET /me" "PUT /me" "DELETE /me" "PUT /me/evidence" "PUT /me/public"; do
  RID=$(aws apigatewayv2 get-routes --api-id "$API_ID" --query "Items[?RouteKey=='$rk'].RouteId | [0]" --output text)
  if [ -z "$RID" ] || [ "$RID" = "None" ]; then
    aws apigatewayv2 create-route --api-id "$API_ID" --route-key "$rk" --target "integrations/$INT_ID" \
      --authorization-type JWT --authorizer-id "$AUTH_ID" >/dev/null
  fi
done
ENDPOINT="$(aws apigatewayv2 get-api --api-id "$API_ID" --query ApiEndpoint --output text)/"
echo "api $ENDPOINT"
sleep 2; curl -s -o /dev/null -w "health check: HTTP %{http_code}\n" "${ENDPOINT}health"

# ---------- 6. Site -> Amplify ----------
mkdir -p "$HERE/build"
sed "s#__ENDPOINT__#${ENDPOINT}#g" "$HERE/site/index.html" > "$HERE/build/index.html"
( cd "$HERE/build" && rm -f ../site.zip && zip -q ../site.zip index.html )
APP_ID=$(aws amplify list-apps --query "apps[?name=='$NAME' || name=='$AMPLIFY_APP_NAME'].appId | [0]" --output text)
if [ -z "$APP_ID" ] || [ "$APP_ID" = "None" ]; then
  APP_ID=$(aws amplify create-app --name "$NAME" --platform WEB --query app.appId --output text)
else
  aws amplify update-app --app-id "$APP_ID" --name "$NAME" >/dev/null
fi
aws amplify get-branch --app-id "$APP_ID" --branch-name "$BRANCH" >/dev/null 2>&1 || aws amplify create-branch --app-id "$APP_ID" --branch-name "$BRANCH" --stage PRODUCTION >/dev/null
DEP=$(aws amplify create-deployment --app-id "$APP_ID" --branch-name "$BRANCH")
JOB_ID=$(echo "$DEP" | jq -r .jobId); UPLOAD=$(echo "$DEP" | jq -r .zipUploadUrl)
curl -sS -T "$HERE/site.zip" "$UPLOAD" -H "Content-Type: application/zip"
aws amplify start-deployment --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$JOB_ID" >/dev/null
for i in $(seq 1 40); do
  ST=$(aws amplify get-job --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$JOB_ID" --query job.summary.status --output text)
  [ "$ST" = "SUCCEED" ] && break; [ "$ST" = "FAILED" ] && { echo "amplify deploy failed"; exit 1; }; sleep 5
done
SITE_URL="https://$BRANCH.$APP_ID.amplifyapp.com"

# ---------- 7. Optional: remove the v0.1 'disha' stack (empty) ----------
if [ "${CLEANUP_DISHA:-0}" = "1" ]; then
  OLD_API=$(aws apigatewayv2 get-apis --query "Items[?Name=='disha-responses'].ApiId | [0]" --output text)
  [ -n "$OLD_API" ] && [ "$OLD_API" != "None" ] && aws apigatewayv2 delete-api --api-id "$OLD_API" && echo "removed old api"
  aws lambda delete-function --function-name disha-responses >/dev/null 2>&1 && echo "removed old lambda" || true
  aws dynamodb delete-table --table-name disha-responses >/dev/null 2>&1 && echo "removed old table" || true
  aws iam delete-role-policy --role-name disha-responses-lambda --policy-name ddb >/dev/null 2>&1 || true
  aws iam detach-role-policy --role-name disha-responses-lambda --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole >/dev/null 2>&1 || true
  aws iam delete-role --role-name disha-responses-lambda >/dev/null 2>&1 && echo "removed old role" || true
fi

# ---------- 8. Report ----------
cat > "$HERE/outputs.json" <<EOF
{"site_url":"$SITE_URL","api":"$ENDPOINT","health":"${ENDPOINT}health","export_json":"${ENDPOINT}export?key=$EXPORT_KEY","export_csv":"${ENDPOINT}export?key=$EXPORT_KEY&format=csv","region":"$REGION","app_id":"$APP_ID","api_id":"$API_ID","pool_id":"$POOL_ID","client_id":"$CLIENT_ID","table":"$NAME"}
EOF
echo; echo "================ DONE ================"
echo "Site:          $SITE_URL"
echo "API:           $ENDPOINT"
echo "Health:        ${ENDPOINT}health"
echo "Cognito pool:  $POOL_ID   (users: aws cognito-idp list-users --user-pool-id $POOL_ID)"
[ -z "${CI:-}" ] && echo "Export (CSV):  ${ENDPOINT}export?key=$EXPORT_KEY&format=csv"
echo "Note: Cognito's default email sender allows about 50 OTP emails a day per pool; move to Amazon SES for more."
