"""NextRung API: one Lambda behind an API Gateway HTTP API.

Public routes
  GET  /health                       {"ok":true,"users":n,"responses_and_events":n,"version":...}
  POST /responses                    anonymous opt-in quiz responses (legacy, kept)
  POST /events   {sid,event,props?}  anonymous funnel events
  GET  /export?key=K[&kind=events|responses][&format=csv]
  GET  /admin/digest?key=K           run the weekly mentor/student digest (used by the ops agent)
  POST /auth/start   {email}         creates the Cognito user if needed, sends an email OTP  -> {session}
  POST /auth/verify  {email,code,session} -> {id_token, refresh_token, expires_in, user}
  POST /auth/refresh {refresh_token} -> {id_token, expires_in}
  GET  /public/{slug}                a graduate's public standing (only if they enabled it)

JWT-protected (API Gateway JWT authorizer; claims in requestContext.authorizer.jwt.claims)
  Student
    GET  /me                          profile + evidence (+ checks, mentor, notes)
    PUT  /me                          {name?, college?, headline?, answers?, ranking?, primary_path?, handles?}
    DELETE /me                        delete account data
    PUT  /me/evidence                 {skill_id, evidence:{type,url,note,self_level}} or {skill_id, remove:true}; verifies the link
    POST /me/evidence/verify          {skill_id} re-run verification
    PUT  /me/public                   {enabled:bool, slug?}
    POST /me/mentor                   {code} link to a mentor by code | {accept:true} accept a pending mentor | {decline:true}
    DELETE /me/mentor                 unlink
    GET  /checks/{skill}              6 items for a calibration check (answers stay here)
    POST /checks/{skill}              {answers:{item_id: option_index}, predicted:int} -> graded result with explanations
  Mentor (role mentor|admin)
    GET  /mentor/students             standing cards for linked students
    GET  /mentor/students/{id}        one student in full
    POST /mentor/students/{id}/note   {text}
    PUT  /mentor/students/{id}/review {skill_id, verdict:"ok"|"redo", comment}
    PUT  /mentor/students/{id}/plan   {focus_skill?} | {confirm_skill} | {unconfirm_skill}
  Admin (email in ADMIN_EMAILS or role admin)
    GET  /admin/overview              counts + funnel
    GET  /admin/users[?q=]            users list
    PUT  /admin/users/{id}            {role:"student"|"mentor"|"admin"}
    PUT  /admin/assign                {student_email, mentor_email} -> pending request the student accepts
    POST /admin/digest                run the digest now

Guardrail philosophy: nothing counts because a student said it; proofs are verified where the source allows it,
skills get a short calibration check (predicted vs actual score), and the mentor sees the gaps, not just the links.
"""
import json, os, time, uuid, csv, io, re, secrets, base64, random, urllib.request, urllib.error, urllib.parse
import boto3
from boto3.dynamodb.conditions import Key

REGION = os.environ.get("AWS_REGION", "ap-south-1")
TABLE = os.environ["TABLE"]                       # per-user data
RESP_TABLE = os.environ.get("RESP_TABLE", "")     # anonymous responses + events
EXPORT_KEY = os.environ.get("EXPORT_KEY", "")
POOL_ID = os.environ.get("POOL_ID", "")
CLIENT_ID = os.environ.get("CLIENT_ID", "")
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
SES_FROM = os.environ.get("SES_FROM", "")         # verified SES sender; emails are skipped when empty
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
VERSION = "0.4.0"

ddb = boto3.resource("dynamodb", region_name=REGION)
T = ddb.Table(TABLE)
RT = ddb.Table(RESP_TABLE) if RESP_TABLE else None
idp = boto3.client("cognito-idp", region_name=REGION)
_ses = None

MAX_BODY = 40_000
ANSWER_FIELDS = {"branch", "college_tier", "graduation_year", "region", "relocation", "runway_months",
                 "interests", "priority", "preparation", "enjoyed"}
EVIDENCE_TYPES = {"repo", "deployed_url", "cert", "score", "document", "other", "mentor_confirmed"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,30}$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,38}$")
ROLES = {"student", "mentor", "admin"}
CHECK_N = 6
CHECK_COOLDOWN_S = 24 * 3600

try:
    with open(os.path.join(os.path.dirname(__file__), "checks.json"), encoding="utf-8") as _f:
        CHECKS = json.load(_f)
except Exception:  # noqa
    CHECKS = {}


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


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def days_since(iso):
    if not iso:
        return None
    try:
        return (time.time() - time.mktime(time.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S"))) / 86400
    except Exception:  # noqa
        return None


def clip(s, n):
    return str(s or "")[:n]


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
    prof = get_profile(sub) or new_profile(sub, email)
    return resp(200, {"ok": True, "id_token": ar["IdToken"], "refresh_token": ar.get("RefreshToken"),
                      "expires_in": ar.get("ExpiresIn"), "user": own_view(prof)})


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


# ---------------- profiles ----------------
def new_profile(sub, email):
    prof = {"pk": f"USER#{sub}", "sk": "PROFILE", "email": email, "created": now(), "updated": now(),
            "last_active": now(), "public": {"enabled": False}, "role": "student"}
    T.put_item(Item=prof)
    return prof


def get_profile(sub):
    return T.get_item(Key={"pk": f"USER#{sub}", "sk": "PROFILE"}).get("Item")


def touch(sub):
    try:
        T.update_item(Key={"pk": f"USER#{sub}", "sk": "PROFILE"}, UpdateExpression="SET last_active = :u",
                      ConditionExpression="attribute_exists(pk)", ExpressionAttributeValues={":u": now()})
    except Exception:  # noqa
        pass


def role_of(prof):
    if prof and str(prof.get("email", "")).lower() in ADMIN_EMAILS:
        return "admin"
    return (prof or {}).get("role") or "student"


def items_of(sub, prefix):
    q = T.query(KeyConditionExpression=Key("pk").eq(f"USER#{sub}") & Key("sk").begins_with(prefix))
    return q.get("Items", [])


def get_evidence(sub):
    out = {}
    for i in items_of(sub, "EV#"):
        out[i["sk"][3:]] = {k: i.get(k) for k in ("type", "url", "note", "added", "self_level", "verify", "review") if i.get(k) is not None}
    return out


def get_checks(sub):
    out = {}
    for i in items_of(sub, "CHK#"):
        if i.get("result"):
            out[i["sk"][4:]] = dict(i["result"], attempts=i.get("attempts", 1))
    return out


def get_notes(sub, limit=20):
    notes = sorted(items_of(sub, "NOTE#"), key=lambda x: x["sk"], reverse=True)[:limit]
    return [{"at": n.get("at"), "by": n.get("by_name"), "text": n.get("text")} for n in notes]


def public_view(prof):
    v = {"name": prof.get("name"), "college": prof.get("college"), "headline": prof.get("headline"),
         "primary_path": prof.get("primary_path"), "ranking": prof.get("ranking"), "updated": prof.get("updated"),
         "public": prof.get("public", {"enabled": False})}
    v["answers"] = {k: prof.get("answers", {}).get(k) for k in ("branch", "college_tier", "graduation_year", "region")} if prof.get("answers") else None
    return v


def own_view(prof):
    v = public_view(prof)
    v.update({"email": prof.get("email"), "answers": prof.get("answers"), "created": prof.get("created"),
              "role": role_of(prof), "handles": prof.get("handles") or {}, "mentor": prof.get("mentor"),
              "focus_skill": prof.get("focus_skill"), "last_active": prof.get("last_active")})
    if v["role"] in ("mentor", "admin"):
        v["mentor_code"] = prof.get("mentor_code")
    return v


def me_get(event):
    sub = claims_of(event).get("sub")
    prof = get_profile(sub) or new_profile(sub, claims_of(event).get("email", ""))
    if role_of(prof) in ("mentor", "admin") and not prof.get("mentor_code"):
        prof = ensure_mentor_code(prof)
    return resp(200, {"user": own_view(prof), "evidence": get_evidence(sub), "checks": get_checks(sub),
                      "notes": get_notes(sub, 5), "checks_available": {k: len(v["items"]) for k, v in CHECKS.items()}})


def me_put(event):
    sub = claims_of(event).get("sub")
    b = body_of(event)
    prof = get_profile(sub) or new_profile(sub, claims_of(event).get("email", ""))
    for k in ("name", "college", "headline"):
        if k in b:
            prof[k] = clip(b[k], 120)
    if "answers" in b and isinstance(b["answers"], dict):
        prof["answers"] = {k: v for k, v in b["answers"].items() if k in ANSWER_FIELDS}
    if "ranking" in b and isinstance(b["ranking"], list):
        prof["ranking"] = [{"id": clip(r.get("id"), 64), "score": int(r.get("score", 0))} for r in b["ranking"][:9] if isinstance(r, dict)]
    if "primary_path" in b:
        prof["primary_path"] = clip(b["primary_path"], 64)
    if "handles" in b and isinstance(b["handles"], dict):
        h = {}
        for k in ("github", "leetcode", "hackerrank", "linkedin"):
            v = str(b["handles"].get(k) or "").strip().lstrip("@")
            v = re.sub(r"^https?://(www\.)?(github\.com|leetcode\.com/u|leetcode\.com|hackerrank\.com/profile|hackerrank\.com|linkedin\.com/in)/", "", v).strip("/")
            if v and HANDLE_RE.match(v):
                h[k] = v
        prof["handles"] = h
    prof["updated"] = now()
    T.put_item(Item=prof)
    return resp(200, {"ok": True, "user": own_view(prof)})


# ---------------- proof verification ----------------
def _fetch(url, method="GET", timeout=4, headers=None, data=None):
    h = {"User-Agent": "NextRung-verify/0.4 (+https://github.com/mgmanikandan87/nextrung)", "Accept": "application/json, text/html;q=0.8"}
    h.update(headers or {})
    req = urllib.request.Request(url, method=method, headers=h, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(200_000)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:  # noqa
        return 0, b""


def _github_repo(owner, repo, handle):
    st, raw = _fetch(f"https://api.github.com/repos/{owner}/{repo}")
    if st == 403 or st == 429:
        return {"status": "pending", "detail": "GitHub is rate-limiting checks right now; we will re-check later."}
    if st == 404:
        return {"status": "unverified", "detail": "That repository does not exist or is private."}
    if st != 200:
        return {"status": "unverified", "detail": f"Could not reach GitHub (HTTP {st})."}
    j = json.loads(raw or b"{}")
    facts = {"fork": bool(j.get("fork")), "created": str(j.get("created_at", ""))[:10], "pushed": str(j.get("pushed_at", ""))[:10],
             "size_kb": int(j.get("size") or 0), "stars": int(j.get("stargazers_count") or 0), "owner": j.get("owner", {}).get("login")}
    st2, raw2 = _fetch(f"https://api.github.com/repos/{owner}/{repo}/commits?per_page=100")
    if st2 == 200:
        commits = json.loads(raw2 or b"[]")
        dates = sorted({str(c.get("commit", {}).get("author", {}).get("date", ""))[:10] for c in commits if c.get("commit")})
        facts["commits"] = len(commits)
        facts["commit_days"] = len(dates)
        if dates:
            facts["first_commit"], facts["last_commit"] = dates[0], dates[-1]
    owned = bool(handle) and str(facts.get("owner", "")).lower() == handle.lower()
    notes = []
    if facts["fork"]:
        notes.append("it is a fork of someone else's repository")
    if facts.get("commits", 0) and facts.get("commit_days", 0) <= 1:
        notes.append("all commits are on one day")
    if facts.get("commits", 0) < 3:
        notes.append("fewer than 3 commits")
    if owned and not facts["fork"] and facts.get("commits", 0) >= 3 and facts.get("commit_days", 0) >= 2:
        return {"status": "verified", "detail": f"Your repository, {facts['commits']} commits over {facts['commit_days']} days.", "facts": facts}
    if owned:
        return {"status": "partial", "detail": "Your repository, but " + "; ".join(notes) + ".", "facts": facts}
    if not handle:
        return {"status": "partial", "detail": "Repository exists. Add your GitHub username in Settings so we can confirm it is yours." + (" Also " + "; ".join(notes) + "." if notes else ""), "facts": facts}
    return {"status": "partial", "detail": f"Repository exists but is owned by {facts.get('owner')}, not your GitHub username ({handle}).", "facts": facts}


def _leetcode(user, handle):
    q = json.dumps({"query": "query($u:String!){matchedUser(username:$u){username submitStats{acSubmissionNum{difficulty count}}}}", "variables": {"u": user}}).encode()
    st, raw = _fetch("https://leetcode.com/graphql", method="POST", headers={"Content-Type": "application/json", "Referer": "https://leetcode.com"}, data=q)
    if st != 200:
        return {"status": "unverified", "detail": f"Could not reach LeetCode (HTTP {st})."}
    try:
        mu = json.loads(raw)["data"]["matchedUser"]
    except Exception:  # noqa
        mu = None
    if not mu:
        return {"status": "unverified", "detail": "No LeetCode user with that name."}
    counts = {x["difficulty"].lower(): int(x["count"]) for x in mu["submitStats"]["acSubmissionNum"]}
    facts = {"solved": counts.get("all", 0), "easy": counts.get("easy", 0), "medium": counts.get("medium", 0), "hard": counts.get("hard", 0)}
    owned = bool(handle) and handle.lower() == user.lower()
    if owned and facts["solved"] >= 30:
        return {"status": "verified", "detail": f"Your LeetCode profile: {facts['solved']} solved ({facts['medium']} medium).", "facts": facts}
    if owned:
        return {"status": "partial", "detail": f"Your LeetCode profile, {facts['solved']} solved so far (30+ counts as real practice).", "facts": facts}
    return {"status": "partial", "detail": f"Profile exists with {facts['solved']} solved. Add your LeetCode username in Settings to confirm it is yours.", "facts": facts}


def verify_url(url, typ, handles):
    """Best-effort check of a proof link. Never blocks the save; returns a label the student and mentor both see."""
    handles = handles or {}
    try:
        u = urllib.parse.urlparse(url)
    except Exception:  # noqa
        return {"status": "unverified", "detail": "Not a valid link."}
    host = (u.netloc or "").lower().replace("www.", "")
    parts = [p for p in u.path.split("/") if p]
    if host == "github.com" and len(parts) >= 2:
        r = _github_repo(parts[0], parts[1], handles.get("github"))
    elif host == "github.com" and len(parts) == 1:
        st, _ = _fetch(f"https://api.github.com/users/{parts[0]}")
        r = {"status": "partial", "detail": "A GitHub profile, not a specific project. Link the repository itself."} if st == 200 else {"status": "unverified", "detail": "GitHub user not found."}
    elif host == "leetcode.com" and parts:
        user = parts[1] if parts[0] == "u" and len(parts) > 1 else parts[0]
        r = _leetcode(user, handles.get("leetcode"))
    else:
        st, _ = _fetch(url, method="GET", timeout=4)
        if st == 0:
            r = {"status": "unverified", "detail": "The link did not load."}
        elif st in (401, 403):
            r = {"status": "partial", "detail": "The link exists but needs a login to view; a mentor will check it by hand."}
        elif st == 404:
            r = {"status": "unverified", "detail": "The link returns 404 (not found)."}
        elif st >= 400:
            r = {"status": "unverified", "detail": f"The link returned HTTP {st}."}
        elif host in ("coursera.org", "nptel.ac.in", "onlinecourses.nptel.ac.in", "swayam.gov.in", "archive.nptel.ac.in", "credly.com", "hackerrank.com", "kaggle.com", "freecodecamp.org", "linkedin.com"):
            r = {"status": "partial", "detail": "The page loads. We cannot confirm it is yours automatically; a mentor can."}
        else:
            r = {"status": "partial", "detail": "The link loads. A mentor will look at it."}
    r["checked_at"] = now()
    return r


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
    typ = ev.get("type") if ev.get("type") in EVIDENCE_TYPES and ev.get("type") != "mentor_confirmed" else "other"
    url = clip(ev.get("url"), 500)
    if not url or not re.match(r"^https?://", url):
        return resp(400, {"error": "evidence url must start with http(s)://"})
    prof = get_profile(sub) or {}
    item = {"pk": f"USER#{sub}", "sk": f"EV#{skill}", "type": typ, "url": url, "note": clip(ev.get("note"), 300), "added": now()}
    try:
        lvl = int(ev.get("self_level") or 0)
        if 1 <= lvl <= 4:
            item["self_level"] = lvl
    except (TypeError, ValueError):
        pass
    item["verify"] = verify_url(url, typ, prof.get("handles"))
    T.put_item(Item=item)
    T.update_item(Key={"pk": f"USER#{sub}", "sk": "PROFILE"}, UpdateExpression="SET updated = :u, last_active = :u", ExpressionAttributeValues={":u": now()})
    return resp(200, {"ok": True, "verify": item["verify"]})


def me_evidence_verify(event):
    sub = claims_of(event).get("sub")
    skill = re.sub(r"[^a-z0-9_]", "", str(body_of(event).get("skill_id", "")))[:64]
    it = T.get_item(Key={"pk": f"USER#{sub}", "sk": f"EV#{skill}"}).get("Item")
    if not it or not it.get("url"):
        return resp(404, {"error": "no proof for that skill"})
    prof = get_profile(sub) or {}
    v = verify_url(it["url"], it.get("type"), prof.get("handles"))
    T.update_item(Key={"pk": f"USER#{sub}", "sk": f"EV#{skill}"}, UpdateExpression="SET verify = :v", ExpressionAttributeValues={":v": v})
    return resp(200, {"ok": True, "verify": v})


# ---------------- calibration checks ----------------
def checks_get(event, skill):
    sub = claims_of(event).get("sub")
    bank = CHECKS.get(skill)
    if not bank:
        return resp(404, {"error": "no check for this skill yet"})
    key = {"pk": f"USER#{sub}", "sk": f"CHK#{skill}"}
    rec = T.get_item(Key=key).get("Item") or {}
    last = rec.get("result")
    if last and (days_since(last.get("at")) or 99) * 86400 < CHECK_COOLDOWN_S:
        return resp(200, {"cooldown": True, "result": dict(last, attempts=rec.get("attempts", 1))})
    items = list(bank["items"])
    applied = [i for i in items if i.get("level") == "applied"]
    basic = [i for i in items if i.get("level") != "applied"]
    random.shuffle(applied); random.shuffle(basic)
    pick = (applied[:4] + basic)[:CHECK_N] if len(applied) >= 4 else (applied + basic)[:CHECK_N]
    random.shuffle(pick)
    out, perms = [], {}
    for it in pick:
        perm = list(range(4)); random.shuffle(perm)          # perm[i] = original index shown at position i
        perms[it["id"]] = perm
        out.append({"id": it["id"], "level": it.get("level"), "q": it["q"], "options": [it["options"][k] for k in perm]})
    rec.update({"pk": key["pk"], "sk": key["sk"], "pending": {"ids": [i["id"] for i in pick], "perms": perms, "issued": now()}})
    T.put_item(Item=rec)
    return resp(200, {"skill_id": skill, "items": out, "n": len(out), "last": (dict(last, attempts=rec.get("attempts", 1)) if last else None)})


def checks_post(event, skill):
    sub = claims_of(event).get("sub")
    bank = CHECKS.get(skill)
    if not bank:
        return resp(404, {"error": "no check for this skill"})
    key = {"pk": f"USER#{sub}", "sk": f"CHK#{skill}"}
    rec = T.get_item(Key=key).get("Item") or {}
    pend = rec.get("pending")
    if not pend:
        return resp(400, {"error": "start the check first"})
    if (days_since(pend.get("issued")) or 0) > 1:
        return resp(400, {"error": "that check expired, start again"})
    b = body_of(event)
    answers = b.get("answers") if isinstance(b.get("answers"), dict) else {}
    try:
        predicted = max(0, min(int(pend["ids"].__len__()), int(b.get("predicted"))))
    except (TypeError, ValueError):
        return resp(400, {"error": "predicted score required"})
    by_id = {i["id"]: i for i in bank["items"]}
    n, score, review, wrong = 0, 0, [], []
    for iid in pend["ids"]:
        it = by_id.get(iid)
        if not it:
            continue
        n += 1
        perm = [int(x) for x in pend["perms"].get(iid, [0, 1, 2, 3])]
        try:
            chosen_pos = int(answers.get(iid))
        except (TypeError, ValueError):
            chosen_pos = -1
        chosen = perm[chosen_pos] if 0 <= chosen_pos <= 3 else -1
        ok = chosen == int(it["answer"])
        score += 1 if ok else 0
        if not ok:
            wrong.append(iid)
        review.append({"id": iid, "q": it["q"], "your": it["options"][chosen] if chosen >= 0 else None, "correct": it["options"][int(it["answer"])], "ok": ok, "explain": it["explain"]})
    pct = int(round(score / n * 100)) if n else 0
    gap = predicted - score
    result = {"score": score, "n": n, "pct": pct, "predicted": predicted, "gap": gap, "at": now(), "wrong": wrong}
    T.put_item(Item={"pk": key["pk"], "sk": key["sk"], "result": result, "attempts": int(rec.get("attempts", 0)) + 1,
                     "history": (list(rec.get("history", []))[-4:] + [{"at": result["at"], "score": score, "n": n, "predicted": predicted}])})
    touch(sub)
    return resp(200, {"ok": True, "result": dict(result, attempts=int(rec.get("attempts", 0)) + 1), "review": review})


# ---------------- mentor linking ----------------
def ensure_mentor_code(prof):
    if prof.get("mentor_code"):
        return prof
    for _ in range(5):
        code = "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(6))
        try:
            T.put_item(Item={"pk": f"CODE#{code}", "sk": "MENTOR", "sub": prof["pk"][5:]}, ConditionExpression="attribute_not_exists(pk)")
            prof["mentor_code"] = code
            T.put_item(Item=prof)
            return prof
        except T.meta.client.exceptions.ConditionalCheckFailedException:
            continue
    raise RuntimeError("could not allocate mentor code")


def link_mentor(student_sub, mentor_sub, status):
    sp = get_profile(student_sub); mp = get_profile(mentor_sub)
    if not sp or not mp or role_of(mp) not in ("mentor", "admin"):
        raise ValueError("that mentor is not available")
    if student_sub == mentor_sub:
        raise ValueError("you cannot mentor yourself")
    old = (sp.get("mentor") or {}).get("id")
    if old and old != mentor_sub:
        T.delete_item(Key={"pk": f"MENTOR#{old}", "sk": f"STU#{student_sub}"})
    sp["mentor"] = {"id": mentor_sub, "name": mp.get("name") or mp.get("email", "").split("@")[0], "status": status, "since": now()}
    sp["updated"] = now()
    T.put_item(Item=sp)
    T.put_item(Item={"pk": f"MENTOR#{mentor_sub}", "sk": f"STU#{student_sub}", "status": status, "since": now(), "email": sp.get("email"), "name": sp.get("name")})
    return sp, mp


def me_mentor_post(event):
    sub = claims_of(event).get("sub")
    b = body_of(event)
    prof = get_profile(sub)
    if not prof:
        return resp(404, {"error": "no profile"})
    if b.get("accept") or b.get("decline"):
        m = prof.get("mentor") or {}
        if not m or m.get("status") != "pending":
            return resp(400, {"error": "no pending mentor request"})
        if b.get("decline"):
            T.delete_item(Key={"pk": f"MENTOR#{m['id']}", "sk": f"STU#{sub}"})
            prof["mentor"] = None; prof["updated"] = now(); T.put_item(Item=prof)
            return resp(200, {"ok": True, "mentor": None})
        sp, mp = link_mentor(sub, m["id"], "active")
        send_mail(mp.get("email"), "A student accepted you as mentor on NextRung", f"{sp.get('name') or sp.get('email')} accepted your mentoring request.\n\nOpen your mentor page: {SITE_URL}/#/mentor")
        return resp(200, {"ok": True, "mentor": sp["mentor"]})
    code = re.sub(r"[^A-Z0-9]", "", str(b.get("code", "")).upper())[:8]
    if not code:
        return resp(400, {"error": "mentor code required"})
    owner = T.get_item(Key={"pk": f"CODE#{code}", "sk": "MENTOR"}).get("Item")
    if not owner:
        return resp(404, {"error": "no mentor with that code"})
    sp, mp = link_mentor(sub, owner["sub"], "active")
    send_mail(mp.get("email"), "New student linked on NextRung", f"{sp.get('name') or sp.get('email')} linked to you with your mentor code.\n\nOpen your mentor page: {SITE_URL}/#/mentor")
    return resp(200, {"ok": True, "mentor": sp["mentor"]})


def me_mentor_delete(event):
    sub = claims_of(event).get("sub")
    prof = get_profile(sub)
    m = (prof or {}).get("mentor")
    if m:
        T.delete_item(Key={"pk": f"MENTOR#{m['id']}", "sk": f"STU#{sub}"})
        prof["mentor"] = None; prof["updated"] = now(); T.put_item(Item=prof)
    return resp(200, {"ok": True})


# ---------------- mentor console ----------------
def flags_of(prof, evidence, checks):
    f = []
    d = days_since(prof.get("last_active") or prof.get("updated"))
    if d is not None and d >= 14:
        f.append({"id": "inactive", "text": f"No activity for {int(d)} days"})
    if not evidence:
        if (days_since(prof.get("created")) or 0) >= 14:
            f.append({"id": "no_proof", "text": "No proof added in the first two weeks"})
    else:
        days = {str(e.get("added", ""))[:10] for e in evidence.values()}
        if len(evidence) >= 3 and len(days) == 1:
            f.append({"id": "dumped", "text": f"All {len(evidence)} proofs added on one day"})
        bad = [k for k, e in evidence.items() if (e.get("verify") or {}).get("status") == "unverified"]
        if bad:
            f.append({"id": "broken", "text": f"{len(bad)} proof link(s) do not load: " + ", ".join(bad[:3])})
        redo = [k for k, e in evidence.items() if (e.get("review") or {}).get("verdict") == "redo"]
        if redo:
            f.append({"id": "redo", "text": "Asked to redo: " + ", ".join(redo[:3])})
    for k, c in (checks or {}).items():
        lvl = int((evidence.get(k) or {}).get("self_level") or 0)
        if c.get("gap", 0) >= 2:
            f.append({"id": "overconfident", "text": f"{k}: predicted {c['predicted']}/{c['n']}, scored {c['score']}/{c['n']}"})
        if lvl >= 3 and int(c.get("pct", 0)) < 50:
            f.append({"id": "mismatch", "text": f"{k}: rated self 'can do alone' but scored {c['pct']}%"})
    return f


def student_view(sub, full=False):
    prof = get_profile(sub)
    if not prof:
        return None
    ev = get_evidence(sub); ch = get_checks(sub)
    v = {"id": sub, "name": prof.get("name"), "email": prof.get("email"), "college": prof.get("college"),
         "primary_path": prof.get("primary_path"), "ranking": prof.get("ranking"), "created": prof.get("created"),
         "last_active": prof.get("last_active") or prof.get("updated"), "handles": prof.get("handles") or {},
         "answers": {k: (prof.get("answers") or {}).get(k) for k in ("branch", "college_tier", "graduation_year", "region", "runway_months")},
         "focus_skill": prof.get("focus_skill"), "mentor": prof.get("mentor"), "public": prof.get("public"),
         "evidence": ev, "checks": ch, "flags": flags_of(prof, ev, ch), "notes": get_notes(sub, 20 if full else 3)}
    return v


def mentor_guard(event):
    sub = claims_of(event).get("sub")
    prof = get_profile(sub)
    if role_of(prof) not in ("mentor", "admin"):
        return None, resp(403, {"error": "mentor access only"})
    return prof, None


def mentor_can_see(mentor_prof, student_sub):
    if role_of(mentor_prof) == "admin":
        return True
    link = T.get_item(Key={"pk": f"MENTOR#{mentor_prof['pk'][5:]}", "sk": f"STU#{student_sub}"}).get("Item")
    return bool(link and link.get("status") == "active")


def mentor_students(event):
    mp, err = mentor_guard(event)
    if err:
        return err
    q = T.query(KeyConditionExpression=Key("pk").eq(f"MENTOR#{mp['pk'][5:]}") & Key("sk").begins_with("STU#"))
    out = []
    for link in q.get("Items", []):
        sv = student_view(link["sk"][4:])
        if sv:
            sv["link_status"] = link.get("status"); out.append(sv)
    out.sort(key=lambda s: (-(len(s["flags"])), s.get("last_active") or ""))
    return resp(200, {"students": out, "mentor_code": mp.get("mentor_code"), "role": role_of(mp)})


def mentor_student(event, sid):
    mp, err = mentor_guard(event)
    if err:
        return err
    if not mentor_can_see(mp, sid):
        return resp(403, {"error": "not your student"})
    sv = student_view(sid, full=True)
    return resp(200, {"student": sv}) if sv else resp(404, {"error": "not found"})


def mentor_note(event, sid):
    mp, err = mentor_guard(event)
    if err:
        return err
    if not mentor_can_see(mp, sid):
        return resp(403, {"error": "not your student"})
    text = clip(body_of(event).get("text"), 1000).strip()
    if not text:
        return resp(400, {"error": "text required"})
    by = mp.get("name") or mp.get("email", "").split("@")[0]
    T.put_item(Item={"pk": f"USER#{sid}", "sk": f"NOTE#{now()}#{secrets.token_hex(2)}", "text": text, "by": mp["pk"][5:], "by_name": by, "at": now()})
    sp = get_profile(sid) or {}
    send_mail(sp.get("email"), f"Note from your mentor {by} on NextRung", f"{text}\n\nOpen your plan: {SITE_URL}/#/me")
    return resp(200, {"ok": True})


def mentor_review(event, sid):
    mp, err = mentor_guard(event)
    if err:
        return err
    if not mentor_can_see(mp, sid):
        return resp(403, {"error": "not your student"})
    b = body_of(event)
    skill = re.sub(r"[^a-z0-9_]", "", str(b.get("skill_id", "")))[:64]
    verdict = b.get("verdict") if b.get("verdict") in ("ok", "redo") else None
    if not skill or not verdict:
        return resp(400, {"error": "skill_id and verdict ok|redo required"})
    key = {"pk": f"USER#{sid}", "sk": f"EV#{skill}"}
    if not T.get_item(Key=key).get("Item"):
        return resp(404, {"error": "no proof for that skill"})
    by = mp.get("name") or mp.get("email", "").split("@")[0]
    review = {"verdict": verdict, "comment": clip(b.get("comment"), 500), "by": by, "at": now()}
    T.update_item(Key=key, UpdateExpression="SET review = :r", ExpressionAttributeValues={":r": review})
    sp = get_profile(sid) or {}
    send_mail(sp.get("email"), f"Your mentor reviewed your {skill} proof", (("Looks good." if verdict == "ok" else "Please redo this one.") + ("\n\n" + review["comment"] if review["comment"] else "") + f"\n\nOpen your plan: {SITE_URL}/#/me/plan"))
    return resp(200, {"ok": True, "review": review})


def mentor_plan(event, sid):
    mp, err = mentor_guard(event)
    if err:
        return err
    if not mentor_can_see(mp, sid):
        return resp(403, {"error": "not your student"})
    b = body_of(event)
    by = mp.get("name") or mp.get("email", "").split("@")[0]
    sp = get_profile(sid)
    if not sp:
        return resp(404, {"error": "not found"})
    if "focus_skill" in b:
        sp["focus_skill"] = re.sub(r"[^a-z0-9_]", "", str(b.get("focus_skill") or ""))[:64] or None
    if b.get("confirm_skill"):
        skill = re.sub(r"[^a-z0-9_]", "", str(b["confirm_skill"]))[:64]
        T.put_item(Item={"pk": f"USER#{sid}", "sk": f"EV#{skill}", "type": "mentor_confirmed", "url": "", "note": clip(b.get("comment"), 300), "added": now(),
                         "verify": {"status": "verified", "detail": f"Confirmed in person by mentor {by}.", "checked_at": now()},
                         "review": {"verdict": "ok", "comment": clip(b.get("comment"), 500), "by": by, "at": now()}})
    if b.get("unconfirm_skill"):
        skill = re.sub(r"[^a-z0-9_]", "", str(b["unconfirm_skill"]))[:64]
        it = T.get_item(Key={"pk": f"USER#{sid}", "sk": f"EV#{skill}"}).get("Item")
        if it and it.get("type") == "mentor_confirmed":
            T.delete_item(Key={"pk": f"USER#{sid}", "sk": f"EV#{skill}"})
    sp["updated"] = now()
    T.put_item(Item=sp)
    return resp(200, {"ok": True, "student": student_view(sid)})


# ---------------- admin ----------------
def admin_guard(event):
    sub = claims_of(event).get("sub")
    prof = get_profile(sub)
    if role_of(prof) != "admin":
        return None, resp(403, {"error": "admin only"})
    return prof, None


def scan_all(table, **kw):
    items, start = [], None
    while True:
        page = table.scan(**(dict(kw, ExclusiveStartKey=start) if start else kw))
        items.extend(page.get("Items", []))
        start = page.get("LastEvaluatedKey")
        if not start:
            return items


def admin_overview(event):
    _, err = admin_guard(event)
    if err:
        return err
    profs = scan_all(T, FilterExpression=Key("sk").eq("PROFILE"))
    evs = scan_all(T, FilterExpression=Key("sk").begins_with("EV#"))
    chks = scan_all(T, FilterExpression=Key("sk").begins_with("CHK#"))
    links = scan_all(T, FilterExpression=Key("sk").begins_with("STU#"))
    roles = {}
    for p in profs:
        roles[role_of(p)] = roles.get(role_of(p), 0) + 1
    act7 = sum(1 for p in profs if (days_since(p.get("last_active") or p.get("updated")) or 99) <= 7)
    paths = {}
    for p in profs:
        if p.get("primary_path"):
            paths[p["primary_path"]] = paths.get(p["primary_path"], 0) + 1
    vstat = {}
    for e in evs:
        s = (e.get("verify") or {}).get("status") or "none"
        vstat[s] = vstat.get(s, 0) + 1
    funnel = {"7d": {}, "30d": {}, "all": {}}
    sessions = {"7d": set(), "30d": set(), "all": set()}
    if RT:
        for it in scan_all(RT):
            if it.get("kind") != "event":
                continue
            d = days_since(it.get("ts")) or 0
            for w, lim in (("7d", 7), ("30d", 30), ("all", 1e9)):
                if d <= lim:
                    funnel[w][it["event"]] = funnel[w].get(it["event"], 0) + 1
                    sessions[w].add(it.get("sid"))
    return resp(200, {"users": len(profs), "roles": roles, "active_7d": act7, "paths": paths, "proofs": len(evs), "proof_status": vstat,
                      "checks_taken": sum(1 for c in chks if c.get("result")), "mentor_links": len([l for l in links if l.get("status") == "active"]),
                      "pending_links": len([l for l in links if l.get("status") == "pending"]),
                      "funnel": funnel, "sessions": {k: len(v) for k, v in sessions.items()}, "version": VERSION})


def admin_users(event):
    _, err = admin_guard(event)
    if err:
        return err
    qs = event.get("queryStringParameters") or {}
    q = (qs.get("q") or "").lower()
    profs = scan_all(T, FilterExpression=Key("sk").eq("PROFILE"))
    out = []
    for p in profs:
        if q and q not in (str(p.get("email", "")) + " " + str(p.get("name", "")) + " " + str(p.get("college", ""))).lower():
            continue
        out.append({"id": p["pk"][5:], "email": p.get("email"), "name": p.get("name"), "college": p.get("college"), "role": role_of(p),
                    "primary_path": p.get("primary_path"), "created": p.get("created"), "last_active": p.get("last_active") or p.get("updated"),
                    "mentor": p.get("mentor"), "mentor_code": p.get("mentor_code"), "handles": p.get("handles") or {}})
    out.sort(key=lambda x: x.get("last_active") or "", reverse=True)
    return resp(200, {"users": out[:500], "total": len(out)})


def admin_user_put(event, sid):
    _, err = admin_guard(event)
    if err:
        return err
    role = body_of(event).get("role")
    if role not in ROLES:
        return resp(400, {"error": "role must be student|mentor|admin"})
    prof = get_profile(sid)
    if not prof:
        return resp(404, {"error": "not found"})
    prof["role"] = role
    prof["updated"] = now()
    T.put_item(Item=prof)
    if role in ("mentor", "admin"):
        prof = ensure_mentor_code(prof)
        send_mail(prof.get("email"), "You are now a mentor on NextRung", f"Your mentor code is {prof['mentor_code']}. Students enter it under Settings > Mentor to link with you.\n\nMentor page: {SITE_URL}/#/mentor")
    return resp(200, {"ok": True, "user": {"id": sid, "role": role, "mentor_code": prof.get("mentor_code")}})


def admin_assign(event):
    _, err = admin_guard(event)
    if err:
        return err
    b = body_of(event)
    se, me_ = norm_email(b.get("student_email")), norm_email(b.get("mentor_email"))
    profs = scan_all(T, FilterExpression=Key("sk").eq("PROFILE"))
    sp = next((p for p in profs if str(p.get("email", "")).lower() == se), None)
    mp = next((p for p in profs if str(p.get("email", "")).lower() == me_), None)
    if not sp or not mp:
        return resp(404, {"error": "student or mentor has not signed in yet"})
    if role_of(mp) not in ("mentor", "admin"):
        return resp(400, {"error": "that user is not a mentor; set the role first"})
    sp, mp = link_mentor(sp["pk"][5:], mp["pk"][5:], "pending")
    send_mail(sp.get("email"), f"{mp.get('name') or 'A mentor'} would like to mentor you on NextRung", f"Open Settings > Mentor to allow or decline. Nothing is shared until you allow it.\n\n{SITE_URL}/#/me/settings")
    return resp(200, {"ok": True, "mentor": sp["mentor"]})


# ---------------- email + digest ----------------
def send_mail(to, subject, text):
    global _ses
    if not SES_FROM or not to:
        return False
    try:
        if _ses is None:
            _ses = boto3.client("ses", region_name=REGION)
        _ses.send_email(Source=SES_FROM, Destination={"ToAddresses": [to]},
                        Message={"Subject": {"Data": subject[:200]}, "Body": {"Text": {"Data": text + "\n\nNextRung is free and open source. Reply to this email if something looks wrong."}}})
        return True
    except Exception as e:  # noqa
        print("MAIL ERROR", repr(e))
        return False


def digest_run():
    """Weekly: each mentor gets their stuck students; each active student gets this week's nudge. Returns what was (or would be) sent."""
    profs = scan_all(T, FilterExpression=Key("sk").eq("PROFILE"))
    by_sub = {p["pk"][5:]: p for p in profs}
    links = scan_all(T, FilterExpression=Key("sk").begins_with("STU#"))
    per_mentor = {}
    for l in links:
        if l.get("status") == "active":
            per_mentor.setdefault(l["pk"][7:], []).append(l["sk"][4:])
    sent = {"mentors": [], "students": []}
    for msub, studs in per_mentor.items():
        mp = by_sub.get(msub)
        if not mp:
            continue
        lines = []
        for ssub in studs:
            sv = student_view(ssub)
            if sv and sv["flags"]:
                lines.append(f"- {sv.get('name') or sv.get('email')}: " + "; ".join(f["text"] for f in sv["flags"][:3]))
        if lines:
            ok = send_mail(mp.get("email"), f"NextRung: {len(lines)} of your {len(studs)} students need a look", "\n".join(lines) + f"\n\nMentor page: {SITE_URL}/#/mentor")
            sent["mentors"].append({"mentor": mp.get("email"), "students_flagged": len(lines), "sent": ok})
    for p in profs:
        if role_of(p) != "student" or not p.get("primary_path"):
            continue
        d = days_since(p.get("last_active") or p.get("updated")) or 0
        if 5 <= d <= 60:
            ok = send_mail(p.get("email"), "NextRung: your one thing this week", f"It has been {int(d)} days. Open your plan, do the one item at the top, add the proof link. Ten minutes a day beats a weekend binge.\n\n{SITE_URL}/#/me")
            sent["students"].append({"student": p.get("email"), "days_idle": int(d), "sent": ok})
    sent["email_enabled"] = bool(SES_FROM)
    return sent


def admin_digest(event):
    qs = event.get("queryStringParameters") or {}
    if not (EXPORT_KEY and qs.get("key") == EXPORT_KEY):
        _, err = admin_guard(event)
        if err:
            return err
    return resp(200, digest_run())


# ---------------- public page, anonymous responses, events, export, health ----------------
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
    prof = get_profile(sub) or {}
    slug = (prof.get("public") or {}).get("slug")
    m = prof.get("mentor") or {}
    with T.batch_writer() as bw:
        for it in T.query(KeyConditionExpression=Key("pk").eq(f"USER#{sub}")).get("Items", []):
            bw.delete_item(Key={"pk": it["pk"], "sk": it["sk"]})
        if slug:
            bw.delete_item(Key={"pk": f"SLUG#{slug}", "sk": "OWNER"})
        if m.get("id"):
            bw.delete_item(Key={"pk": f"MENTOR#{m['id']}", "sk": f"STU#{sub}"})
        if prof.get("mentor_code"):
            bw.delete_item(Key={"pk": f"CODE#{prof['mentor_code']}", "sk": "MENTOR"})
    return resp(200, {"ok": True})


def public_get(event, slug):
    slug = (slug or "").lower()
    own = T.get_item(Key={"pk": f"SLUG#{slug}", "sk": "OWNER"}).get("Item")
    if not own:
        return resp(404, {"error": "not found"})
    prof = get_profile(own["sub"])
    if not prof or not (prof.get("public") or {}).get("enabled"):
        return resp(404, {"error": "not found"})
    ev = get_evidence(own["sub"])
    for e in ev.values():
        e.pop("self_level", None)
        if e.get("review"):
            e["review"] = {"verdict": e["review"].get("verdict"), "by": e["review"].get("by")}
    return resp(200, {"user": public_view(prof), "evidence": ev})


def responses_post(event):
    if not RT:
        return resp(404, {"error": "disabled"})
    body = body_of(event)
    if body.get("consent") is not True:
        return resp(400, {"error": "consent required"})
    answers = {k: v for k, v in (body.get("answers") or {}).items() if k in ANSWER_FIELDS}
    ranking = [{"id": clip(r.get("id"), 64), "score": int(r.get("score", 0))} for r in (body.get("ranking") or [])[:9] if isinstance(r, dict)]
    contact = {k: clip(v, 200) for k, v in (body.get("contact") or {}).items() if k in ("name", "college", "email") and v}
    item = {"id": str(uuid.uuid4()), "ts": now(), "answers": answers, "ranking": ranking, "contact": contact,
            "site_version": clip(body.get("site_version"), 32), "ua": clip(event.get("headers", {}).get("user-agent"), 200)}
    RT.put_item(Item=item)
    return resp(200, {"ok": True, "id": item["id"]})


EVENTS = {"start", "quiz_done", "results_viewed", "path_picked", "plan_viewed", "signin_started", "signed_in", "proof_added", "public_on",
          "share_clicked", "report_printed", "job_opened", "learn_opened", "check_started", "check_done", "mentor_linked", "handle_set",
          "mentor_note", "mentor_review", "admin_viewed"}


def events_post(event):
    if not RT:
        return resp(404, {"error": "disabled"})
    b = body_of(event)
    ev = clip(b.get("event"), 40)
    if ev not in EVENTS:
        return resp(400, {"error": "unknown event"})
    sid = re.sub(r"[^a-zA-Z0-9_-]", "", str(b.get("sid", "")))[:40] or "anon"
    props = b.get("props") if isinstance(b.get("props"), dict) else {}
    props = {str(k)[:40]: str(v)[:120] for k, v in list(props.items())[:10]}
    RT.put_item(Item={"id": str(uuid.uuid4()), "ts": now(), "kind": "event", "sid": sid, "event": ev, "props": props,
                      "lang": clip(b.get("lang"), 8), "site_version": clip(b.get("site_version"), 32)})
    return resp(200, {"ok": True})


def export_get(event):
    qs = event.get("queryStringParameters") or {}
    if not EXPORT_KEY or qs.get("key") != EXPORT_KEY or not RT:
        return resp(403, {"error": "export key required"})
    kind = qs.get("kind", "responses")
    items = [i for i in scan_all(RT) if (i.get("kind") == "event") == (kind == "events")]
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
    return resp(200, {"ok": True, "users": users, "responses_and_events": n, "version": VERSION, "checks": len(CHECKS), "email": bool(SES_FROM)})


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
        if path == "/admin/digest" and method in ("GET", "POST"): return admin_digest(event)
        if path == "/auth/start" and method == "POST": return auth_start(event)
        if path == "/auth/verify" and method == "POST": return auth_verify(event)
        if path == "/auth/refresh" and method == "POST": return auth_refresh(event)
        if path.startswith("/public/") and method == "GET": return public_get(event, path[len("/public/"):])
        sub = claims_of(event).get("sub")
        if not sub:
            return resp(401, {"error": "sign in"})
        seg = path.strip("/").split("/")
        if seg[0] in ("me", "checks", "mentor"):
            touch(sub)
        if path == "/me":
            if method == "GET": return me_get(event)
            if method == "PUT": return me_put(event)
            if method == "DELETE": return me_delete(event)
        if path == "/me/evidence" and method == "PUT": return me_evidence(event)
        if path == "/me/evidence/verify" and method == "POST": return me_evidence_verify(event)
        if path == "/me/public" and method == "PUT": return me_public(event)
        if path == "/me/mentor" and method == "POST": return me_mentor_post(event)
        if path == "/me/mentor" and method == "DELETE": return me_mentor_delete(event)
        if seg[0] == "checks" and len(seg) == 2:
            skill = re.sub(r"[^a-z0-9_]", "", seg[1])[:64]
            if method == "GET": return checks_get(event, skill)
            if method == "POST": return checks_post(event, skill)
        if seg[0] == "mentor" and len(seg) >= 2 and seg[1] == "students":
            if len(seg) == 2 and method == "GET": return mentor_students(event)
            if len(seg) == 3 and method == "GET": return mentor_student(event, seg[2])
            if len(seg) == 4 and seg[3] == "note" and method == "POST": return mentor_note(event, seg[2])
            if len(seg) == 4 and seg[3] == "review" and method == "PUT": return mentor_review(event, seg[2])
            if len(seg) == 4 and seg[3] == "plan" and method == "PUT": return mentor_plan(event, seg[2])
        if seg[0] == "admin":
            if path == "/admin/overview" and method == "GET": return admin_overview(event)
            if path == "/admin/users" and method == "GET": return admin_users(event)
            if len(seg) == 3 and seg[1] == "users" and method == "PUT": return admin_user_put(event, seg[2])
            if path == "/admin/assign" and method == "PUT": return admin_assign(event)
        return resp(404, {"error": "no such route", "path": path})
    except ValueError as e:
        return resp(400, {"error": str(e)})
    except Exception as e:  # noqa
        print("ERROR", repr(e))
        return resp(500, {"error": "server error", "detail": str(e)[:200]})
