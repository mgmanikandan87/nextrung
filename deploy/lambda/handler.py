"""NextRung API: one Lambda behind an API Gateway HTTP API.

Public routes
  GET  /health                       {"ok":true,"users":n,"responses":n}
  POST /responses                    anonymous opt-in quiz responses (legacy, kept)
  POST /events   {sid,event,props?}  anonymous funnel events (start, quiz_done, path_picked, plan_viewed, signed_in, proof_added)
  GET  /export?key=K[&format=csv]    export anonymous responses
  POST /auth/start   {email}         creates the Cognito user if needed, sends an email OTP  -> {session}
  POST /auth/verify  {email,code,session} -> {id_token, refresh_token, expires_in, user}
  POST /auth/refresh {refresh_token} -> {id_token, expires_in}
  GET  /public/{slug}                a graduate's public standing (only if they enabled it)

JWT-protected routes (API Gateway JWT authorizer; claims in requestContext.authorizer.jwt.claims)
  GET  /me                           profile + evidence
  PUT  /me                           {name?, college?, headline?, answers?, ranking?, primary_path?}
  PUT  /me/evidence                  {skill_id, evidence:{type,url,note}} or {skill_id, remove:true}
  PUT  /me/public                    {enabled:bool, slug?}
  DELETE /me                         delete account data (Cognito user stays; deleted on request)
"""
import json, os, time, uuid, csv, io, re, secrets, base64
import boto3
from boto3.dynamodb.conditions import Key

REGION = os.environ.get("AWS_REGION", "ap-south-1")
TABLE = os.environ["TABLE"]                       # per-user data
RESP_TABLE = os.environ.get("RESP_TABLE", "")     # anonymous responses (legacy)
EXPORT_KEY = os.environ.get("EXPORT_KEY", "")
POOL_ID = os.environ.get("POOL_ID", "")
CLIENT_ID = os.environ.get("CLIENT_ID", "")

ddb = boto3.resource("dynamodb", region_name=REGION)
T = ddb.Table(TABLE)
RT = ddb.Table(RESP_TABLE) if RESP_TABLE else None
idp = boto3.client("cognito-idp", region_name=REGION)

MAX_BODY = 40_000
ANSWER_FIELDS = {"branch", "college_tier", "graduation_year", "region", "relocation", "runway_months",
                 "interests", "priority", "preparation", "enjoyed"}
EVIDENCE_TYPES = {"repo", "deployed_url", "cert", "score", "document", "other"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,30}$")


def resp(code, body, content_type="application/json"):
    return {"statusCode": code,
            "headers": {"Content-Type": content_type, "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
                        "Access-Control-Allow-Headers": "content-type,authorization"},
            "body": body if isinstance(body, str) else json.dumps(body, default=_default)}


def _default(o):
    from decimal import Decimal
    if isinstance(o, Decimal):
        return int(o) if o == int(o) else float(o)
    return str(o)


def body_of(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", "replace")
    if len(raw) > MAX_BODY:
        raise ValueError("body too large")
    return json.loads(raw) if raw else {}


def claims_of(event):
    return (event.get("requestContext", {}).get("authorizer", {}) or {}).get("jwt", {}).get("claims", {}) or {}


def norm_email(e):
    e = (e or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e) or len(e) > 254:
        raise ValueError("invalid email")
    return e


# ---------------- auth ----------------
def ensure_user(email):
    try:
        u = idp.admin_get_user(UserPoolId=POOL_ID, Username=email)
        return u.get("UserStatus")
    except idp.exceptions.UserNotFoundException:
        pass
    idp.admin_create_user(UserPoolId=POOL_ID, Username=email, MessageAction="SUPPRESS",
                          UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}])
    return "NEW"


def start_otp(email):
    """Kick off Cognito's passwordless email OTP. Returns the challenge session."""
    def initiate():
        return idp.admin_initiate_auth(UserPoolId=POOL_ID, ClientId=CLIENT_ID, AuthFlow="USER_AUTH",
                                       AuthParameters={"USERNAME": email, "PREFERRED_CHALLENGE": "EMAIL_OTP"})
    r = initiate()
    if r.get("ChallengeName") != "EMAIL_OTP":
        # user created by admin without a password can sit in FORCE_CHANGE_PASSWORD; set an unused random
        # permanent password to move it to CONFIRMED, then retry. The password is never returned or used.
        idp.admin_set_user_password(UserPoolId=POOL_ID, Username=email,
                                    Password="Nr!" + secrets.token_urlsafe(24), Permanent=True)
        r = initiate()
    if r.get("ChallengeName") != "EMAIL_OTP":
        raise RuntimeError("unexpected challenge " + str(r.get("ChallengeName")))
    return r["Session"]


def auth_start(event):
    email = norm_email(body_of(event).get("email"))
    ensure_user(email)
    session = start_otp(email)
    return resp(200, {"ok": True, "session": session})


def auth_verify(event):
    b = body_of(event)
    email = norm_email(b.get("email"))
    code = re.sub(r"\D", "", str(b.get("code", "")))
    session = b.get("session")
    if not code or not session:
        return resp(400, {"error": "code and session required"})
    try:
        r = idp.admin_respond_to_auth_challenge(UserPoolId=POOL_ID, ClientId=CLIENT_ID, ChallengeName="EMAIL_OTP",
                                                Session=session, ChallengeResponses={"USERNAME": email, "EMAIL_OTP_CODE": code})
    except (idp.exceptions.CodeMismatchException, idp.exceptions.NotAuthorizedException):
        return resp(401, {"error": "wrong code"})
    except idp.exceptions.ExpiredCodeException:
        return resp(401, {"error": "code expired, request a new one"})
    ar = r.get("AuthenticationResult")
    if not ar:
        return resp(401, {"error": "wrong code", "challenge": r.get("ChallengeName")})
    sub = _sub_from_id_token(ar["IdToken"])
    prof = get_profile(sub)
    if not prof:
        prof = {"pk": f"USER#{sub}", "sk": "PROFILE", "email": email, "created": now(), "updated": now(), "public": {"enabled": False}}
        T.put_item(Item=prof)
    return resp(200, {"ok": True, "id_token": ar["IdToken"], "refresh_token": ar.get("RefreshToken"),
                      "expires_in": ar.get("ExpiresIn"), "user": public_view(prof, own=True)})


def auth_refresh(event):
    rt = body_of(event).get("refresh_token")
    if not rt:
        return resp(400, {"error": "refresh_token required"})
    try:
        r = idp.admin_initiate_auth(UserPoolId=POOL_ID, ClientId=CLIENT_ID, AuthFlow="REFRESH_TOKEN_AUTH",
                                    AuthParameters={"REFRESH_TOKEN": rt})
    except idp.exceptions.NotAuthorizedException:
        return resp(401, {"error": "sign in again"})
    ar = r["AuthenticationResult"]
    return resp(200, {"ok": True, "id_token": ar["IdToken"], "expires_in": ar.get("ExpiresIn")})


def _sub_from_id_token(tok):
    payload = tok.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))["sub"]


# ---------------- profile ----------------
def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def get_profile(sub):
    return T.get_item(Key={"pk": f"USER#{sub}", "sk": "PROFILE"}).get("Item")


def get_evidence(sub):
    q = T.query(KeyConditionExpression=Key("pk").eq(f"USER#{sub}") & Key("sk").begins_with("EV#"))
    return {i["sk"][3:]: {"type": i.get("type"), "url": i.get("url"), "note": i.get("note"), "added": i.get("added")} for i in q.get("Items", [])}


def public_view(prof, own=False):
    v = {"name": prof.get("name"), "college": prof.get("college"), "headline": prof.get("headline"),
         "primary_path": prof.get("primary_path"), "ranking": prof.get("ranking"), "updated": prof.get("updated"),
         "public": prof.get("public", {"enabled": False})}
    if own:
        v.update({"email": prof.get("email"), "answers": prof.get("answers"), "created": prof.get("created")})
    else:
        v["answers"] = {k: prof.get("answers", {}).get(k) for k in ("branch", "college_tier", "graduation_year", "region")} if prof.get("answers") else None
    return v


def me_get(event):
    sub = claims_of(event).get("sub")
    prof = get_profile(sub)
    if not prof:
        email = claims_of(event).get("email", "")
        prof = {"pk": f"USER#{sub}", "sk": "PROFILE", "email": email, "created": now(), "updated": now(), "public": {"enabled": False}}
        T.put_item(Item=prof)
    return resp(200, {"user": public_view(prof, own=True), "evidence": get_evidence(sub)})


def me_put(event):
    sub = claims_of(event).get("sub")
    b = body_of(event)
    prof = get_profile(sub) or {"pk": f"USER#{sub}", "sk": "PROFILE", "email": claims_of(event).get("email", ""),
                                "created": now(), "public": {"enabled": False}}
    for k in ("name", "college", "headline"):
        if k in b:
            prof[k] = str(b[k] or "")[:120]
    if "answers" in b and isinstance(b["answers"], dict):
        prof["answers"] = {k: v for k, v in b["answers"].items() if k in ANSWER_FIELDS}
    if "ranking" in b and isinstance(b["ranking"], list):
        prof["ranking"] = [{"id": str(r.get("id"))[:64], "score": int(r.get("score", 0))} for r in b["ranking"][:9] if isinstance(r, dict)]
    if "primary_path" in b:
        prof["primary_path"] = str(b["primary_path"] or "")[:64]
    prof["updated"] = now()
    T.put_item(Item=prof)
    return resp(200, {"ok": True, "user": public_view(prof, own=True)})


def me_evidence(event):
    sub = claims_of(event).get("sub")
    b = body_of(event)
    skill = re.sub(r"[^a-z0-9_]", "", str(b.get("skill_id", "")))[:64]
    if not skill:
        return resp(400, {"error": "skill_id required"})
    if b.get("remove"):
        T.delete_item(Key={"pk": f"USER#{sub}", "sk": f"EV#{skill}"})
        return resp(200, {"ok": True})
    ev = b.get("evidence") or {}
    typ = ev.get("type") if ev.get("type") in EVIDENCE_TYPES else "other"
    url = str(ev.get("url", ""))[:500]
    if url and not re.match(r"^https?://", url):
        return resp(400, {"error": "evidence url must start with http(s)://"})
    item = {"pk": f"USER#{sub}", "sk": f"EV#{skill}", "type": typ, "url": url, "note": str(ev.get("note", ""))[:300], "added": now()}
    T.put_item(Item=item)
    T.update_item(Key={"pk": f"USER#{sub}", "sk": "PROFILE"}, UpdateExpression="SET updated = :u", ExpressionAttributeValues={":u": now()})
    return resp(200, {"ok": True})


def me_public(event):
    sub = claims_of(event).get("sub")
    b = body_of(event)
    prof = get_profile(sub)
    if not prof:
        return resp(404, {"error": "no profile"})
    enabled = bool(b.get("enabled"))
    slug = str(b.get("slug") or prof.get("public", {}).get("slug") or "").strip().lower()
    if enabled:
        if not slug:
            base = re.sub(r"[^a-z0-9]+", "-", (prof.get("name") or prof.get("email", "").split("@")[0]).lower()).strip("-")[:20] or "engineer"
            slug = f"{base}-{secrets.token_hex(2)}"
        if not SLUG_RE.match(slug):
            return resp(400, {"error": "slug: 3 to 31 chars, lowercase letters, digits, hyphens"})
        old = prof.get("public", {}).get("slug")
        if old != slug:
            try:
                T.put_item(Item={"pk": f"SLUG#{slug}", "sk": "OWNER", "sub": sub}, ConditionExpression="attribute_not_exists(pk)")
            except T.meta.client.exceptions.ConditionalCheckFailedException:
                return resp(409, {"error": "that link is taken, try another"})
            if old:
                T.delete_item(Key={"pk": f"SLUG#{old}", "sk": "OWNER"})
    prof["public"] = {"enabled": enabled, "slug": slug or None}
    prof["updated"] = now()
    T.put_item(Item=prof)
    return resp(200, {"ok": True, "public": prof["public"]})


def me_delete(event):
    sub = claims_of(event).get("sub")
    q = T.query(KeyConditionExpression=Key("pk").eq(f"USER#{sub}"))
    prof = get_profile(sub) or {}
    slug = (prof.get("public") or {}).get("slug")
    with T.batch_writer() as bw:
        for it in q.get("Items", []):
            bw.delete_item(Key={"pk": it["pk"], "sk": it["sk"]})
        if slug:
            bw.delete_item(Key={"pk": f"SLUG#{slug}", "sk": "OWNER"})
    return resp(200, {"ok": True})


def public_get(event, slug):
    slug = (slug or "").lower()
    own = T.get_item(Key={"pk": f"SLUG#{slug}", "sk": "OWNER"}).get("Item")
    if not own:
        return resp(404, {"error": "not found"})
    prof = get_profile(own["sub"])
    if not prof or not (prof.get("public") or {}).get("enabled"):
        return resp(404, {"error": "not found"})
    return resp(200, {"user": public_view(prof), "evidence": get_evidence(own["sub"])})


# ---------------- anonymous responses (legacy) ----------------
def responses_post(event):
    if not RT:
        return resp(404, {"error": "disabled"})
    body = body_of(event)
    if body.get("consent") is not True:
        return resp(400, {"error": "consent required"})
    answers = {k: v for k, v in (body.get("answers") or {}).items() if k in ANSWER_FIELDS}
    ranking = [{"id": str(r.get("id"))[:64], "score": int(r.get("score", 0))} for r in (body.get("ranking") or [])[:9] if isinstance(r, dict)]
    contact = {k: str(v)[:200] for k, v in (body.get("contact") or {}).items() if k in ("name", "college", "email") and v}
    item = {"id": str(uuid.uuid4()), "ts": now(), "answers": answers, "ranking": ranking, "contact": contact,
            "site_version": str(body.get("site_version", ""))[:32], "ua": str(event.get("headers", {}).get("user-agent", ""))[:200]}
    RT.put_item(Item=item)
    return resp(200, {"ok": True, "id": item["id"]})


EVENTS = {"start", "quiz_done", "results_viewed", "path_picked", "plan_viewed", "signin_started", "signed_in", "proof_added", "public_on", "share_clicked", "report_printed", "job_opened", "learn_opened"}


def events_post(event):
    if not RT:
        return resp(404, {"error": "disabled"})
    b = body_of(event)
    ev = str(b.get("event", ""))[:40]
    if ev not in EVENTS:
        return resp(400, {"error": "unknown event"})
    sid = re.sub(r"[^a-zA-Z0-9_-]", "", str(b.get("sid", "")))[:40] or "anon"
    props = b.get("props") if isinstance(b.get("props"), dict) else {}
    props = {str(k)[:40]: str(v)[:120] for k, v in list(props.items())[:10]}
    RT.put_item(Item={"id": str(uuid.uuid4()), "ts": now(), "kind": "event", "sid": sid, "event": ev, "props": props,
                      "lang": str(b.get("lang", ""))[:8], "site_version": str(b.get("site_version", ""))[:32]})
    return resp(200, {"ok": True})


def export_get(event):
    qs = event.get("queryStringParameters") or {}
    if not EXPORT_KEY or qs.get("key") != EXPORT_KEY or not RT:
        return resp(403, {"error": "export key required"})
    kind = qs.get("kind", "responses")
    items, start = [], None
    while True:
        kw = {"ExclusiveStartKey": start} if start else {}
        page = RT.scan(**kw)
        items.extend(page.get("Items", []))
        start = page.get("LastEvaluatedKey")
        if not start:
            break
    items = [i for i in items if (i.get("kind") == "event") == (kind == "events")]
    items.sort(key=lambda x: x.get("ts", ""))
    if kind == "events":
        if qs.get("format") == "csv":
            out = io.StringIO(); w = csv.writer(out); w.writerow(["ts", "sid", "event", "lang", "site_version", "props"])
            for it in items:
                w.writerow([it.get("ts"), it.get("sid"), it.get("event"), it.get("lang"), it.get("site_version"), json.dumps(it.get("props", {}), default=str)])
            return resp(200, out.getvalue(), "text/csv")
        return resp(200, {"count": len(items), "items": items})
    if qs.get("format") == "csv":
        out = io.StringIO()
        cols = ["id", "ts", "site_version"] + sorted(ANSWER_FIELDS) + ["rank1", "rank2", "rank3", "name", "college", "email"]
        w = csv.DictWriter(out, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for it in items:
            a = it.get("answers", {}) or {}; r = it.get("ranking", []) or []; c = it.get("contact", {}) or {}
            row = {"id": it.get("id"), "ts": it.get("ts"), "site_version": it.get("site_version")}
            for k in ANSWER_FIELDS:
                v = a.get(k); row[k] = "|".join(v) if isinstance(v, list) else v
            for i in range(3):
                row[f"rank{i+1}"] = (r[i].get("id") + ":" + str(r[i].get("score"))) if i < len(r) else ""
            row.update({"name": c.get("name"), "college": c.get("college"), "email": c.get("email")})
            w.writerow(row)
        return resp(200, out.getvalue(), "text/csv")
    return resp(200, {"count": len(items), "items": items})


def health():
    users = T.scan(Select="COUNT", FilterExpression=Key("sk").eq("PROFILE"))["Count"]
    n = RT.scan(Select="COUNT")["Count"] if RT else 0
    return resp(200, {"ok": True, "users": users, "responses_and_events": n, "version": "0.3.0"})


# ---------------- router ----------------
def handler(event, context):
    http = event.get("requestContext", {}).get("http", {})
    method, path = http.get("method", "GET"), (event.get("rawPath") or "/").rstrip("/") or "/"
    if method == "OPTIONS":
        return resp(204, "")
    try:
        if path == "/health" and method == "GET": return health()
        if path == "/responses" and method == "POST": return responses_post(event)
        if path == "/events" and method == "POST": return events_post(event)
        if path == "/export" and method == "GET": return export_get(event)
        if path == "/auth/start" and method == "POST": return auth_start(event)
        if path == "/auth/verify" and method == "POST": return auth_verify(event)
        if path == "/auth/refresh" and method == "POST": return auth_refresh(event)
        if path.startswith("/public/") and method == "GET": return public_get(event, path[len("/public/"):])
        if path == "/me":
            if not claims_of(event).get("sub"): return resp(401, {"error": "sign in"})
            if method == "GET": return me_get(event)
            if method == "PUT": return me_put(event)
            if method == "DELETE": return me_delete(event)
        if path == "/me/evidence" and method == "PUT":
            if not claims_of(event).get("sub"): return resp(401, {"error": "sign in"})
            return me_evidence(event)
        if path == "/me/public" and method == "PUT":
            if not claims_of(event).get("sub"): return resp(401, {"error": "sign in"})
            return me_public(event)
        return resp(404, {"error": "no such route", "path": path})
    except ValueError as e:
        return resp(400, {"error": str(e)})
    except Exception as e:  # noqa
        print("ERROR", repr(e))
        return resp(500, {"error": "server error", "detail": str(e)[:200]})
