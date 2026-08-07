"""Post-deploy smoke test: is the revision that just shipped actually alive?

    uv run python scripts/smoke_test.py https://vital-api-....run.app

Run it straight after `gcloud run deploy`. Exits non-zero on failure, so it
can be chained:

    gcloud run deploy ... && uv run python scripts/smoke_test.py $URL

WHY THIS EXISTS
---------------
A deploy that reports success can still be broken in ways only a real
request reveals: a secret that did not mount, a missing CORS header, a
schema migration that failed. Today that gap was discovered by opening the
app and seeing a white screen — the slowest possible detector, and only
because somebody happened to look.

Checks the SHAPE of the deployment, not business logic. The test suite
covers behaviour; this covers "did the configuration land".
"""
import argparse
import sys
import urllib.error
import urllib.request

TIMEOUT = 20


def check(name: str, url: str, expect, headers: dict | None = None,
          method: str | None = None) -> bool:
    request = urllib.request.Request(url, headers=headers or {},
                                     method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            status, body = response.status, response.read().decode()[:400]
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read().decode()[:400]
    except Exception as exc:
        print(f"  FAIL  {name}: unreachable ({type(exc).__name__})")
        return False

    ok = status in expect if isinstance(expect, (set, list)) else status == expect
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}: {status}"
          + ("" if ok else f"  {body[:120]}"))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", help="service URL, no trailing slash")
    parser.add_argument("--origin", default="https://vital-agent.vercel.app",
                        help="origin used for the CORS preflight check")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    print(f"\nSmoke testing {base}")
    results = []

    # 1. The container is up and serving.
    results.append(check("health", f"{base}/health", 200))

    # 2. Identity is enforced. A 200 here would mean AUTH_REQUIRED did not
    #    take effect — user data readable by anyone.
    results.append(check("auth is enforced", f"{base}/memories", {401, 403}))

    # 3. The CORS preflight the browser will actually send. This is the
    #    check that would have caught the outage where every request failed
    #    while the server looked perfectly healthy.
    results.append(check(
        "CORS preflight", f"{base}/session", {200, 204},
        headers={
            "Origin": args.origin,
            "Access-Control-Request-Method": "GET",
            # Every header the client sends. Miss one in allow_headers and
            # the browser rejects the response before the app sees it.
            "Access-Control-Request-Headers":
                "authorization,content-type,x-utc-offset,x-geo-lat,x-geo-lng,x-geo-label",
        }))

    # 4. The API description parses — a route registered wrongly shows up
    #    here rather than on first use.
    results.append(check("openapi", f"{base}/openapi.json", 200))

    # 5. The scheduled job endpoint refuses an unauthenticated caller.
    #    POST, not GET: a GET only proves the route exists and would pass
    #    with 405 even if the token guard were removed entirely. A 200 here
    #    means anybody can make the app notify all of its users.
    results.append(check("brief job is guarded", f"{base}/jobs/morning-brief",
                         {401, 503}, method="POST"))

    passed = sum(1 for r in results if r)
    print(f"\n{passed}/{len(results)} passed")
    if passed != len(results):
        print("Roll back with:  gcloud run services update-traffic vital-api "
              "--region us-central1 --to-revisions PREVIOUS=100")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
