"""Scale test, part 2: time the API against the seeded hms_scale database (NFR-12).

Budgets: p95 under 500 ms for normal API calls; under 2 s for dashboards and reports.
Run it through scripts/scale-test.sh, which sets HMS_DB_NAME=hms_scale for this process.

The API runs in-process (FastAPI TestClient, real PostgreSQL, real RLS roles); one client sends
requests one after another. That measures how long the server needs per request at the target
DATA size. It does not measure 500 concurrent users: on one laptop the load generator and the
server would share the same CPU, so that number would describe the laptop, not the design.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from itertools import cycle
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import transaction
from app.main import create_app
from tests.support import Org, add_person
from tests.test_platform_auth import bearer, new_owner, sign_in

API = "/api/v1"
NORMAL_MS, DASHBOARD_MS = 500, 2000


@dataclass
class Result:
    name: str
    budget: int
    times: list[float]
    status: int

    @property
    def p95(self) -> float:
        return statistics.quantiles(self.times, n=20, method="inclusive")[18]

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.p95 < self.budget


def measure(client: TestClient, name: str, budget: int, paths: list[str], who: list[dict[str, str]], n: int) -> Result:
    urls, people = cycle(paths), cycle(who)
    status = 200
    for _ in range(3):  # warm-up: connection pool, query plans, Python imports
        r = client.get(next(urls), headers=next(people))
        status = r.status_code if r.status_code != 200 else status
    times = []
    for _ in range(n):
        url = next(urls)
        t = time.perf_counter()
        r = client.get(url, headers=next(people))
        times.append((time.perf_counter() - t) * 1000)
        if r.status_code != 200:
            status = r.status_code
            print(f"  {name}: {url} -> {r.status_code} {r.text[:200]}", file=sys.stderr)
    return Result(name, budget, times, status)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=30, help="timed requests per endpoint")
    args = ap.parse_args()
    if not get_settings().hms_db_name.startswith("hms_scale"):
        raise SystemExit("refusing: run with HMS_DB_NAME=hms_scale (scripts/scale-test.sh)")
    client = TestClient(create_app(run_checks=False), base_url="https://testserver")

    with transaction(None, role="hms_worker") as db:
        org_id, slug = db.execute(text("SELECT id, slug FROM organizations ORDER BY slug LIMIT 1")).one()
        quiet_id, quiet_slug = db.execute(
            text("SELECT id, slug FROM organizations ORDER BY slug OFFSET 1 LIMIT 1")
        ).one()
    org = Org(id=org_id, slug=slug)
    run = uuid.uuid4().hex[:6].upper()  # bench accounts are new on every run (--bench-only reruns)
    sa = add_person(org, "bench-admin", roles=("super_admin",), code=f"B{run}-A").headers()
    hostels = client.get(f"{API}/admin/hostels", headers=sa).json()
    hostels = hostels["items"] if isinstance(hostels, dict) else hostels
    h = hostels[0]["id"]
    # A second organization the bench never writes audit entries for: its newest audit rows are
    # older than everyone else's, the worst case for paging the audit log (found in Phase 10, D65).
    quiet = add_person(Org(id=quiet_id, slug=quiet_slug), "bench-quiet", roles=("super_admin",), code=f"B{run}-Q")
    quiet_sa = quiet.headers()
    hm = add_person(org, "bench-admin2", roles=("super_admin",), code=f"B{run}-M").headers()
    po = bearer(sign_in(client, new_owner()))
    # Exports are rate-limited per user (04 §5.10: 10 an hour), so report timings rotate through
    # separate admin accounts; the limiter itself is part of every timed request.
    exporters = [
        add_person(org, f"bench-exporter-{k}", roles=("super_admin",), code=f"B{run}-X{k}").headers() for k in range(40)
    ]

    def ids(path: str, key: str = "id", count: int = 10, **params: Any) -> list[str]:
        body = client.get(f"{API}{path}", headers=sa, params=params).json()
        items: list[dict[str, Any]] = body["items"] if isinstance(body, dict) else body
        assert items, f"no items from {path}"
        return [str(i[key]) for i in items[:count]]

    students = ids("/admin/students", count=20)
    payments = ids("/admin/payments")
    complaints = ids("/admin/complaints")

    N, D = NORMAL_MS, DASHBOARD_MS
    plan: list[tuple[str, int, list[str], Any]] = [
        ("me", N, ["/me"], sa),
        ("hostels list", N, ["/admin/hostels"], sa),
        ("bed board (1 hostel, ~1,380 beds)", N, [f"/admin/hostels/{h}/bed-board"], sa),
        ("students list, first page", N, ["/admin/students"], sa),
        ("students search by name", N, ["/admin/students?q=Kumar", "/admin/students?q=Sharma"], sa),
        ("students search by code", N, ["/admin/students?q=S04321", "/admin/students?q=S00077"], sa),
        ("students list, a second administrator", N, ["/admin/students"], hm),
        ("student detail", N, [f"/admin/students/{s}" for s in students], sa),
        ("student ledger", N, [f"/admin/students/{s}/ledger" for s in students], sa),
        ("student balance", N, [f"/admin/students/{s}/balance" for s in students], sa),
        ("allocations list", N, ["/admin/allocations"], sa),
        ("payments list", N, ["/admin/payments"], sa),
        ("payment detail", N, [f"/admin/payments/{p}" for p in payments], sa),
        ("complaints list", N, ["/admin/complaints"], sa),
        ("complaint detail", N, [f"/admin/complaints/{c}" for c in complaints], sa),
        ("leave requests", N, ["/admin/leave-requests"], sa),
        ("audit log, first page", N, ["/admin/audit-logs"], sa),
        ("audit log, quiet organization", N, ["/admin/audit-logs"], quiet_sa),
        ("platform: organizations", N, ["/platform/organizations"], po),
        ("platform: audit log", N, ["/platform/audit-logs"], po),
        ("home overview", D, ["/admin/overview"], sa),
        ("dashboard: platform health", D, ["/platform/health"], po),
        ("report: payments export.csv", D, ["/admin/payments/export.csv"], exporters),
    ]
    results = [
        measure(client, name, b, [API + p for p in paths], hdr if isinstance(hdr, list) else [hdr], args.requests)
        for name, b, paths, hdr in plan
    ]

    print(f"\n{'endpoint':42} {'budget':>7} {'p50':>7} {'p95':>7} {'max':>7}  result")
    for r in results:
        verdict = "ok" if r.ok else (f"HTTP {r.status}" if r.status != 200 else "OVER BUDGET")
        print(
            f"{r.name:42} {r.budget:>5}ms {statistics.median(r.times):>5.0f}ms {r.p95:>5.0f}ms "
            f"{max(r.times):>5.0f}ms  {verdict}"
        )
    failed = [r.name for r in results if not r.ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} within budget" + (f"; failed: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
