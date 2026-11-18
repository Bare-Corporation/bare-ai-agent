#!/usr/bin/env python3
"""Submit every URL in a site's sitemap to IndexNow (Bing / ChatGPT Search / Copilot / Yandex).

Flow per domain:
  1. GET https://<host>/<key>.txt and require HTTP 200 whose body equals the key.
     IndexNow rejects a submission whose key file is missing or mismatched, so this
     is checked first to turn a silent 403 into a clear local error.
  2. GET https://<host>/sitemap.xml and extract every <loc> URL, keeping only
     URLs on the same host.
  3. POST the batch to https://api.indexnow.org/indexnow as JSON.

The IndexNow key is NOT a secret. It is a public verification token that is served
openly at /<key>.txt on each domain, so it is safe to keep as the default here.

Exit codes: 0 all domains submitted, 1 at least one domain failed, 2 usage error.

Usage:
    ping_indexnow.py                      # all domains
    ping_indexnow.py --dry-run            # fetch + parse only, no submission
    ping_indexnow.py --host bare-ai.net   # one domain
"""
import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

ENDPOINT = "https://api.indexnow.org/indexnow"
DEFAULT_KEY = os.environ.get("INDEXNOW_KEY", "9c9f70c07063fb67b2911298ad0cf7d6")
UA = "Bare-AI-IndexNow/1.0 (+https://www.cloudintegrationcorporation.com)"
TIMEOUT = 40

DOMAINS = [
    "www.cloudintegrationcorporation.com",
    "bare-ai.net",
    "bare-connectfi.net",
]

CTX = ssl.create_default_context()


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, context=CTX, timeout=TIMEOUT) as resp:
        return resp.getcode(), resp.read().decode("utf-8", "replace")


def http_post_json(url, payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=TIMEOUT) as resp:
            return resp.getcode(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def extract_locs(xml_text):
    return re.findall(r"<loc>\s*(.*?)\s*</loc>", xml_text, re.S)


def submit_domain(host, key, dry_run=False):
    key_url = "https://{0}/{1}.txt".format(host, key)
    sitemap_url = "https://{0}/sitemap.xml".format(host)
    print("=" * 70)
    print("DOMAIN:", host)

    try:
        code, body = http_get(key_url)
    except Exception as exc:
        print("  key file FAILED:", exc)
        return False
    body = body.strip()
    if code != 200 or body != key:
        print("  key file BAD: status={0} body_matches={1}".format(code, body == key))
        return False
    print("  key file OK:", key_url, "->", code)

    try:
        code, xml = http_get(sitemap_url)
    except Exception as exc:
        print("  sitemap FAILED:", exc)
        return False
    if code != 200:
        print("  sitemap BAD: status", code)
        return False

    urls = []
    seen = set()
    for loc in extract_locs(xml):
        loc = loc.strip()
        if not loc.startswith("http"):
            continue
        if re.sub(r"^https?://", "", loc).split("/")[0] != host:
            continue  # IndexNow requires all URLs on the submitted host
        if loc not in seen:
            seen.add(loc)
            urls.append(loc)

    print("  sitemap OK:", len(urls), "URLs on this host")
    if not urls:
        print("  nothing to submit")
        return False

    if dry_run:
        for u in urls[:5]:
            print("    -", u)
        if len(urls) > 5:
            print("    ... and", len(urls) - 5, "more")
        print("  DRY RUN - not submitted")
        return True

    payload = {
        "host": host,
        "key": key,
        "keyLocation": key_url,
        "urlList": urls,
    }
    code, resp = http_post_json(ENDPOINT, payload)
    ok = code in (200, 202)
    print("  POST", ENDPOINT, "->", code, "(200/202 = accepted)" if ok else "(FAILED)")
    if resp.strip():
        print("  response:", resp.strip()[:300])
    return ok


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--host", action="append", default=None)
    ap.add_argument("--key", default=DEFAULT_KEY)
    args = ap.parse_args()

    key = args.key
    if not re.fullmatch(r"[0-9a-f]{32}", key or ""):
        print("ERROR: key must be 32 lowercase hex characters", file=sys.stderr)
        return 2

    hosts = args.host or DOMAINS
    results = {}
    for h in hosts:
        try:
            results[h] = submit_domain(h, key, dry_run=args.dry_run)
        except Exception as exc:
            print("  UNEXPECTED:", exc)
            results[h] = False

    print("=" * 70)
    print("SUMMARY")
    for h, ok in results.items():
        print("  {0:<40} {1}".format(h, "OK" if ok else "FAILED"))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
