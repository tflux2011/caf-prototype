"""Phase A smoke test: drive the full call chain through the entry service.

POSTs ``/signup`` on user-api (published on localhost:8080), which fans out:

    user-api -> auth -> subscription -> payment -> {postgres-primary, stripe}

Exits non-zero if the chain does not return 200. Uses only the standard
library, so it runs without activating the virtualenv.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

URL = "http://localhost:8080/signup"


def main() -> int:
    start = time.perf_counter()
    request = urllib.request.Request(URL, data=b"", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode()
            elapsed_ms = (time.perf_counter() - start) * 1000
            print(f"chain 200 OK in {elapsed_ms:.1f} ms")
            print(json.dumps(json.loads(body), indent=2))
            return 0
    except urllib.error.HTTPError as exc:
        print(f"chain failed: HTTP {exc.code}: {exc.read().decode()}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"chain unreachable: {exc.reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
