"""Disha responses API: one Lambda behind a function URL.

POST /            body: {answers, ranking, consent:true, contact?:{name?, college?, email?}, site_version?}
GET  /?key=K      export all responses as JSON (K = EXPORT_KEY env); &format=csv for CSV
GET  /health      {"ok": true, "count": n}
"""
import json, os, time, uuid, csv, io
import boto3

TABLE = os.environ["TABLE"]
EXPORT_KEY = os.environ.get("EXPORT_KEY", "")
ddb = boto3.resource("dynamodb").Table(TABLE)
MAX_BODY = 20_000

ALLOWED_FIELDS = {"branch", "college_tier", "graduation_year", "region", "relocation", "runway_months",
                  "interests", "priority", "preparation", "enjoyed"}


def _resp(code, body, content_type="application/json"):
    return {"statusCode": code,
            "headers": {"Content-Type": content_type, "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
                        "Access-Control-Allow-Headers": "content-type"},
            "body": body if isinstance(body, str) else json.dumps(body, default=str)}


def handler(event, context):
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "GET")
    path = event.get("rawPath", "/")
    qs = event.get("queryStringParameters") or {}

    if method == "OPTIONS":
        return _resp(204, "")

    if method == "GET" and path.rstrip("/") == "/health":
        n = ddb.scan(Select="COUNT")["Count"]
        return _resp(200, {"ok": True, "count": n})

    if method == "GET":
        if not EXPORT_KEY or qs.get("key") != EXPORT_KEY:
            return _resp(403, {"error": "export key required"})
        items, start = [], None
        while True:
            kw = {"ExclusiveStartKey": start} if start else {}
            page = ddb.scan(**kw)
            items.extend(page.get("Items", []))
            start = page.get("LastEvaluatedKey")
            if not start:
                break
        items.sort(key=lambda x: x.get("ts", ""))
        if qs.get("format") == "csv":
            out = io.StringIO()
            cols = ["id", "ts", "site_version", "branch", "college_tier", "graduation_year", "region", "relocation",
                    "runway_months", "interests", "priority", "preparation", "enjoyed", "rank1", "rank2", "rank3",
                    "name", "college", "email"]
            w = csv.DictWriter(out, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for it in items:
                a = it.get("answers", {}) or {}
                r = it.get("ranking", []) or []
                c = it.get("contact", {}) or {}
                row = {"id": it.get("id"), "ts": it.get("ts"), "site_version": it.get("site_version")}
                for k in ALLOWED_FIELDS:
                    v = a.get(k)
                    row[k] = "|".join(v) if isinstance(v, list) else v
                for i in range(3):
                    row[f"rank{i+1}"] = (r[i].get("id") + ":" + str(r[i].get("score"))) if i < len(r) and isinstance(r[i], dict) else ""
                row.update({"name": c.get("name"), "college": c.get("college"), "email": c.get("email")})
                w.writerow(row)
            return _resp(200, out.getvalue(), "text/csv")
        return _resp(200, {"count": len(items), "items": items})

    if method == "POST":
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            import base64
            raw = base64.b64decode(raw).decode("utf-8", "replace")
        if len(raw) > MAX_BODY:
            return _resp(413, {"error": "body too large"})
        try:
            body = json.loads(raw)
        except Exception:
            return _resp(400, {"error": "invalid json"})
        if body.get("consent") is not True:
            return _resp(400, {"error": "consent required"})
        answers = {k: v for k, v in (body.get("answers") or {}).items() if k in ALLOWED_FIELDS}
        ranking = [{"id": str(r.get("id"))[:64], "score": int(r.get("score", 0))}
                   for r in (body.get("ranking") or [])[:9] if isinstance(r, dict)]
        contact = {k: str(v)[:200] for k, v in (body.get("contact") or {}).items()
                   if k in ("name", "college", "email") and v}
        item = {"id": str(uuid.uuid4()), "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "answers": answers, "ranking": ranking, "contact": contact,
                "site_version": str(body.get("site_version", ""))[:32],
                "ua": str(event.get("headers", {}).get("user-agent", ""))[:200]}
        ddb.put_item(Item=item)
        return _resp(200, {"ok": True, "id": item["id"]})

    return _resp(405, {"error": "method not allowed"})
