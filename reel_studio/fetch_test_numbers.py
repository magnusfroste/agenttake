"""Fetch Skatteverket's open list of test personal numbers into the package.

Run when the image is built (see Dockerfile), so the list sits beside the code
and a number is checked here, never sent anywhere to be looked up. It is not
kept in git. Without it every personal number counts as one that may be
somebody's, which stops a film rather than letting one through.

    python3 -m reel_studio.fetch_test_numbers
"""
from __future__ import annotations

import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# The same file sensitive.TEST_NUMBERS_FILE reads. This module imports nothing
# of AgentTake's, so the image fetches the list in a layer of its own.
TEST_NUMBERS_FILE = Path(__file__).parent / "data" / "skatteverket-test-personnummer.txt.gz"

DATASET = "https://skatteverket.entryscape.net/rowstore/dataset/b4de7df7-63c0-4e7e-bb59-1f156a591763/json"
PAGE = 500


def _get(url: str) -> dict:
    for attempt in range(8):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "AgentTake"})
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if attempt == 7 or error.code not in (429, 500, 502, 503, 504):
                raise
            # The dataset limits how fast it is read: wait as long as it says.
            wait = error.headers.get("Retry-After") if error.headers else None
            time.sleep(min(float(wait), 120.0) if wait and wait.isdigit() else 15.0 * (attempt + 1))
        except (OSError, ValueError):
            if attempt == 7:
                raise
            time.sleep(3.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def fetch() -> set[str]:
    numbers: set[str] = set()
    offset = 0
    while True:
        page = _get(f"{DATASET}?_limit={PAGE}&_offset={offset}")
        rows = page.get("results") or []
        numbers.update(str(row["testpersonnummer"]) for row in rows)
        offset += len(rows)
        time.sleep(0.5)
        if not rows or offset >= int(page.get("resultCount") or 0):
            break
    numbers = {number for number in numbers if re.fullmatch(r"\d{12}", number)}
    # The dataset lists some numbers twice; tens of thousands remain.
    if len(numbers) < 30000:
        raise RuntimeError(f"only {len(numbers)} test numbers: the list looks incomplete")
    return numbers


def main() -> int:
    try:
        numbers = fetch()
    except Exception as exc:  # the build goes on; the check then stops every number
        print(f"test personal numbers not fetched: {exc}", file=sys.stderr)
        return 0
    TEST_NUMBERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(TEST_NUMBERS_FILE, "wb", mtime=0) as listing:
        listing.write(("\n".join(sorted(numbers)) + "\n").encode("ascii"))
    print(f"{len(numbers)} test personal numbers in {TEST_NUMBERS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
