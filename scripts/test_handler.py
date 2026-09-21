#!/usr/bin/env python3
"""Offline tests for deploy/lambda/handler.py with an in-memory DynamoDB stand-in and a stubbed network.
Run: python3 scripts/test_handler.py   (no AWS access needed; exits 1 on failure)"""
import json, os, sys, pathlib, types, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
os.environ.update({"TABLE": "t", "RESP_TABLE": "r", "EXPORT_KEY": "K", "POOL_ID": "p", "CLIENT_ID": "c",
                   "ADMIN_EMAILS": "admin@x.in", "AWS_DEFAULT_REGION": "ap-south-1", "SITE_URL": "https://site.test"})


class Cond:  # minimal stand-in for boto3 Key conditions
    def __init__(self, name): self.name = name
    def eq(self, v): return ("eq", self.name, v)
    def begins_with(self, v): return ("bw", self.name, v)


def _match(cond, item):
    if cond is None: return True
    if isinstance(cond, tuple) and cond[0] == "and": return _match(cond[1], item) and _match(cond[2], item)
    op, name, v = cond; x = item.get(name)
    return (x == v) if op == "eq" else (isinstance(x, str) and x.startswith(v))


class _And(tuple):
    pass


def _and(a, b): return ("and", a, b)
# make ("eq",...) & ("bw",...) work
class CondT(tuple):
    def __and__(self, o): return CondT(("and", tuple(self), tuple(o)))
Cond.eq = lambda self, v: CondT(("eq", self.name, v))
Cond.begins_with = lambda self, v: CondT(("bw", self.name, v))


class CCF(Exception): pass


class FakeTable:
    def __init__(self): self.rows = {}
    class meta:
        class client:
            class exceptions:
                ConditionalCheckFailedException = CCF
    def get_item(self, Key): return {"Item": json.loads(json.dumps(self.rows.get((Key["pk"], Key["sk"]))))} if (Key["pk"], Key["sk"]) in self.rows else {}
    def put_item(self, Item, ConditionExpression=None):
        k = (Item["pk"], Item["sk"]) if "pk" in Item else (Item["id"], "")
        if ConditionExpression == "attribute_not_exists(pk)" and k in self.rows: raise CCF()
        self.rows[k] = json.loads(json.dumps(Item))
        return {}
    def delete_item(self, Key): self.rows.pop((Key["pk"], Key["sk"]), None); return {}
    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression=None):
        k = (Key["pk"], Key["sk"])
        if ConditionExpression and k not in self.rows: raise Exception("cond")
        it = self.rows.setdefault(k, {"pk": Key["pk"], "sk": Key["sk"]})
        for part in UpdateExpression.replace("SET ", "").split(","):
            f, v = [x.strip() for x in part.split("=")]; it[f] = json.loads(json.dumps(ExpressionAttributeValues[v]))
        return {}
    def query(self, KeyConditionExpression): return {"Items": [json.loads(json.dumps(r)) for r in self.rows.values() if _match(KeyConditionExpression, r)]}
    def scan(self, FilterExpression=None, Select=None, ExclusiveStartKey=None):
        items = [json.loads(json.dumps(r)) for r in self.rows.values() if _match(FilterExpression, r)]
        return {"Count": len(items)} if Select == "COUNT" else {"Items": items}
    def batch_writer(self):
        t = self
        class BW:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def delete_item(self, Key): t.delete_item(Key)
        return BW()


tables = {"t": FakeTable(), "r": FakeTable()}
fake_boto3 = types.ModuleType("boto3")
fake_boto3.resource = lambda *a, **k: types.SimpleNamespace(Table=lambda n: tables[n])
fake_boto3.client = lambda *a, **k: types.SimpleNamespace(exceptions=types.SimpleNamespace(UserNotFoundException=Exception, CodeMismatchException=Exception, NotAuthorizedException=Exception, ExpiredCodeException=Exception))
sys.modules["boto3"] = fake_boto3
cond_mod = types.ModuleType("boto3.dynamodb.conditions"); cond_mod.Key = Cond
sys.modules["boto3.dynamodb"] = types.ModuleType("boto3.dynamodb"); sys.modules["boto3.dynamodb.conditions"] = cond_mod
sys.path.insert(0, str(ROOT / "deploy/lambda"))
import handler as H  # noqa: E402

NET = {}
def fake_fetch(url, method="GET", timeout=4, headers=None, data=None):
    best = max((k for k in NET if url.startswith(k)), key=len, default=None)
    return NET[best] if best else (0, b"")
H._fetch = fake_fetch

fails = []
def check(cond, msg):
    if not cond: fails.append(msg); print("FAIL", msg)

def call(method, path, sub=None, email=None, body=None, qs=None):
    ev = {"requestContext": {"http": {"method": method}}, "rawPath": path, "body": json.dumps(body) if body is not None else "", "queryStringParameters": qs or {}}
    if sub: ev["requestContext"]["authorizer"] = {"jwt": {"claims": {"sub": sub, "email": email or f"{sub}@x.in"}}}
    r = H.handler(ev, None)
    return r["statusCode"], (json.loads(r["body"]) if r["headers"]["Content-Type"] == "application/json" and r["body"] else r["body"])

assert H.CHECKS, "checks.json missing: run scripts/build.py first"

# --- health & auth guard
c, b = call("GET", "/health"); check(c == 200 and b["ok"] and b["version"] == H.VERSION, "health")
c, b = call("GET", "/me"); check(c == 401, "me without token is 401")
c, b = call("GET", "/mentor/students", sub="s1"); check(c == 403, "student cannot open mentor console")
c, b = call("GET", "/admin/overview", sub="s1"); check(c == 403, "student cannot open admin")

# --- student profile, handles
c, b = call("PUT", "/me", sub="s1", body={"name": "Asha", "primary_path": "ai_native_software", "handles": {"github": "https://github.com/asha-dev/", "leetcode": "@asha_lc", "linkedin": "bad handle!"}})
check(c == 200 and b["user"]["handles"] == {"github": "asha-dev", "leetcode": "asha_lc"}, "handles normalised: " + str(b))

# --- proof verification: github verified / partial / rate-limited / broken
NET["https://api.github.com/repos/asha-dev/proj"] = (200, json.dumps({"fork": False, "created_at": "2026-05-01T00:00:00Z", "pushed_at": "2026-09-01T00:00:00Z", "size": 120, "stargazers_count": 0, "owner": {"login": "asha-dev"}}).encode())
NET["https://api.github.com/repos/asha-dev/proj/commits"] = (200, json.dumps([{"commit": {"author": {"date": f"2026-0{m}-1{d}T00:00:00Z"}}} for m in (5, 6, 7) for d in (1, 2)]).encode())
c, b = call("PUT", "/me/evidence", sub="s1", body={"skill_id": "python", "evidence": {"type": "repo", "url": "https://github.com/asha-dev/proj", "self_level": 3}})
check(c == 200 and b["verify"]["status"] == "verified" and b["verify"]["facts"]["commits"] == 6, "github owned repo verified: " + str(b))
NET["https://api.github.com/repos/other/proj"] = (200, json.dumps({"fork": True, "owner": {"login": "other"}, "size": 1}).encode())
NET["https://api.github.com/repos/other/proj/commits"] = (200, json.dumps([{"commit": {"author": {"date": "2026-09-01T00:00:00Z"}}}]).encode())
c, b = call("PUT", "/me/evidence", sub="s1", body={"skill_id": "sql", "evidence": {"type": "repo", "url": "https://github.com/other/proj"}})
check(c == 200 and b["verify"]["status"] == "partial" and "owned by other" in b["verify"]["detail"], "someone else's fork is partial: " + str(b))
NET["https://api.github.com/repos/rl/x"] = (403, b"")
c, b = call("PUT", "/me/evidence", sub="s1", body={"skill_id": "dsa", "evidence": {"type": "repo", "url": "https://github.com/rl/x"}})
check(b["verify"]["status"] == "pending", "rate limited is pending")
NET["https://dead.example/x"] = (404, b"")
c, b = call("PUT", "/me/evidence", sub="s1", body={"skill_id": "docker", "evidence": {"type": "other", "url": "https://dead.example/x"}})
check(b["verify"]["status"] == "unverified" and "404" in b["verify"]["detail"], "404 link unverified")
NET["https://leetcode.com/graphql"] = (200, json.dumps({"data": {"matchedUser": {"username": "asha_lc", "submitStats": {"acSubmissionNum": [{"difficulty": "All", "count": 45}, {"difficulty": "Easy", "count": 30}, {"difficulty": "Medium", "count": 15}, {"difficulty": "Hard", "count": 0}]}}}}).encode())
c, b = call("PUT", "/me/evidence", sub="s1", body={"skill_id": "git_github", "evidence": {"type": "score", "url": "https://leetcode.com/u/asha_lc/"}})
check(b["verify"]["status"] == "verified" and b["verify"]["facts"]["solved"] == 45, "leetcode verified: " + str(b))
c, b = call("PUT", "/me/evidence", sub="s1", body={"skill_id": "python", "evidence": {"type": "repo", "url": "ftp://x"}}); check(c == 400, "bad url rejected")
c, b = call("GET", "/me", sub="s1"); check(b["evidence"]["python"]["self_level"] == 3 and b["evidence"]["python"]["verify"]["status"] == "verified" and "python" in b["checks_available"], "me shows verify + self_level")

# --- calibration check: get, cooldown, grade
c, b = call("GET", "/checks/nope", sub="s1"); check(c == 404, "unknown check 404")
c, b = call("POST", "/checks/python", sub="s1", body={"answers": {}, "predicted": 3}); check(c == 400, "grade before start rejected")
c, b = call("GET", "/checks/python", sub="s1"); check(c == 200 and b["n"] == 6 and all("answer" not in i and "explain" not in i for i in b["items"]), "check items served without answers")
items = b["items"]; bank = {i["id"]: i for i in H.CHECKS["python"]["items"]}
answers = {}
for k, it in enumerate(items):  # answer first 4 right (by matching option text), last 2 wrong
    correct_text = bank[it["id"]]["options"][bank[it["id"]]["answer"]]
    pos = it["options"].index(correct_text)
    answers[it["id"]] = pos if k < 4 else (pos + 1) % 4
c, b = call("POST", "/checks/python", sub="s1", body={"answers": answers, "predicted": 6})
check(c == 200 and b["result"]["score"] == 4 and b["result"]["gap"] == 2 and len(b["review"]) == 6 and sum(1 for r in b["review"] if r["ok"]) == 4, "graded 4/6 with gap 2: " + str(b.get("result")))
c, b = call("GET", "/checks/python", sub="s1"); check(b.get("cooldown") is True and b["result"]["attempts"] == 1, "cooldown after a check")
c, b = call("GET", "/me", sub="s1"); check(b["checks"]["python"]["pct"] == 67, "me shows check result")

# --- roles, mentor code, linking by code
c, b = call("GET", "/me", sub="a1", email="admin@x.in"); check(b["user"]["role"] == "admin" and b["user"]["mentor_code"], "admin by email gets a mentor code")
c, b = call("PUT", "/me", sub="m1", email="mentor@x.in", body={"name": "Ravi"}); check(c == 200, "mentor profile")
c, b = call("PUT", "/admin/users/m1", sub="a1", email="admin@x.in", body={"role": "mentor"}); check(c == 200 and b["user"]["mentor_code"], "admin promotes mentor: " + str(b))
code = b["user"]["mentor_code"]
c, b = call("PUT", "/admin/users/m1", sub="m1", email="mentor@x.in", body={"role": "admin"}); check(c == 403, "mentor cannot self-promote")
c, b = call("POST", "/me/mentor", sub="s1", body={"code": "ZZZZZZ"}); check(c == 404, "bad code 404")
c, b = call("POST", "/me/mentor", sub="s1", body={"code": code.lower()}); check(c == 200 and b["mentor"]["status"] == "active" and b["mentor"]["name"] == "Ravi", "student links by code: " + str(b))
c, b = call("GET", "/mentor/students", sub="m1", email="mentor@x.in"); check(c == 200 and len(b["students"]) == 1 and b["students"][0]["evidence"]["python"]["self_level"] == 3 and b["students"][0]["checks"]["python"]["gap"] == 2, "mentor sees linked student standing: " + str(b)[:300])
flags = [f["id"] for f in b["students"][0]["flags"]]; check("overconfident" in flags and "broken" in flags and "dumped" in flags, "flags computed: " + str(flags))
c, b = call("GET", "/mentor/students/s1", sub="m2", email="m2@x.in"); check(c == 403, "unlinked non-mentor blocked")

# --- mentor actions: note, review, confirm, focus
c, b = call("POST", "/mentor/students/s1/note", sub="m1", email="mentor@x.in", body={"text": "Nice repo. Add tests."}); check(c == 200, "note")
c, b = call("PUT", "/mentor/students/s1/review", sub="m1", email="mentor@x.in", body={"skill_id": "sql", "verdict": "redo", "comment": "This is a fork."}); check(c == 200 and b["review"]["verdict"] == "redo", "review redo")
c, b = call("PUT", "/mentor/students/s1/plan", sub="m1", email="mentor@x.in", body={"confirm_skill": "communication_written_english", "focus_skill": "sql", "comment": "Walked through the README on a call."}); check(c == 200 and b["student"]["focus_skill"] == "sql" and b["student"]["evidence"]["communication_written_english"]["type"] == "mentor_confirmed", "confirm + focus: " + str(b)[:200])
c, b = call("GET", "/me", sub="s1"); check(b["user"]["mentor"]["name"] == "Ravi" and b["notes"][0]["text"].startswith("Nice") and b["evidence"]["sql"]["review"]["verdict"] == "redo" and b["user"]["focus_skill"] == "sql", "student sees note, review, focus")
c, b = call("PUT", "/me/public", sub="s1", body={"enabled": True, "slug": "asha-1"}); check(c == 200, "public on")
c, b = call("GET", "/public/asha-1"); check(c == 200 and "self_level" not in b["evidence"]["python"] and b["evidence"]["sql"]["review"] == {"verdict": "redo", "by": "Ravi"}, "public page hides self_level, keeps verdict")

# --- admin assign (pending -> accept), overview, users, digest
c, b = call("PUT", "/me", sub="s2", email="s2@x.in", body={"name": "Kiran", "primary_path": "it_services"})
c, b = call("PUT", "/admin/assign", sub="a1", email="admin@x.in", body={"student_email": "s2@x.in", "mentor_email": "mentor@x.in"}); check(c == 200 and b["mentor"]["status"] == "pending", "admin assign pending: " + str(b))
c, b = call("GET", "/mentor/students", sub="m1", email="mentor@x.in"); check(len(b["students"]) == 2 and {s["link_status"] for s in b["students"]} == {"active", "pending"}, "mentor list shows pending")
c, b = call("GET", "/mentor/students/s2", sub="m1", email="mentor@x.in"); check(c == 403, "pending student not visible until accepted")
c, b = call("POST", "/me/mentor", sub="s2", email="s2@x.in", body={"accept": True}); check(c == 200 and b["mentor"]["status"] == "active", "student accepts")
c, b = call("GET", "/mentor/students/s2", sub="m1", email="mentor@x.in"); check(c == 200 and b["student"]["name"] == "Kiran", "visible after accept")
c, b = call("DELETE", "/me/mentor", sub="s2", email="s2@x.in"); check(c == 200, "unlink")
c, b = call("GET", "/mentor/students", sub="m1", email="mentor@x.in"); check(len(b["students"]) == 1, "unlinked student gone from mentor list")
call("POST", "/events", body={"sid": "x", "event": "quiz_done"}); call("POST", "/events", body={"sid": "y", "event": "check_done"})
c, b = call("GET", "/admin/overview", sub="a1", email="admin@x.in"); check(c == 200 and b["users"] == 4 and b["roles"].get("mentor") == 1 and b["funnel"]["7d"]["quiz_done"] == 1 and b["sessions"]["7d"] == 2 and b["checks_taken"] == 1, "overview: " + str(b))
c, b = call("GET", "/admin/users", sub="a1", email="admin@x.in", qs={"q": "asha"}); check(c == 200 and b["total"] == 1 and b["users"][0]["role"] == "student", "users search")
c, b = call("GET", "/admin/digest", qs={"key": "K"}); check(c == 200 and b["email_enabled"] is False and b["mentors"][0]["students_flagged"] == 1, "digest with export key: " + str(b))
c, b = call("GET", "/admin/digest", qs={"key": "WRONG"}); check(c == 403, "digest with wrong key")
c, b = call("DELETE", "/me", sub="s1"); check(c == 200, "delete account")
c, b = call("GET", "/public/asha-1"); check(c == 404, "public page gone after delete")
c, b = call("GET", "/mentor/students", sub="m1", email="mentor@x.in"); check(len(b["students"]) == 0, "deleted student gone from mentor")

print(f"handler tests: {len(fails)} failures")
sys.exit(1 if fails else 0)
