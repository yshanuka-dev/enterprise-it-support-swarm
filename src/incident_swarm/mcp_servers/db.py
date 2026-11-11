"""Database access layer for MCP servers (PostgreSQL + resilient fallback).

Supports querying live PostgreSQL (via psycopg) with automatic fallback to
an in-memory store for offline development, local CI, and unit testing.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from incident_swarm.config import ROOT, get_settings

log = logging.getLogger("incident_swarm.mcp_servers.db")

# -----------------------------------------------------------------------------
# Default In-Memory Mock Data (matching seed SQL)
# -----------------------------------------------------------------------------
_DEFAULT_SERVICES = [
    {
        "id": "SVC-001",
        "name": "auth-api",
        "description": "Authentication and authorization service",
        "owner_division": "DIV-001",
        "status": "healthy",
        "last_deploy": "2026-05-16 13:16:49",
        "version": "v2.2.15",
    },
    {
        "id": "SVC-002",
        "name": "checkout-api",
        "description": "Payment processing and checkout",
        "owner_division": "DIV-001",
        "status": "degraded",
        "last_deploy": "2026-05-16 13:16:49",
        "version": "v4.5.13",
    },
    {
        "id": "SVC-007",
        "name": "redis-cluster",
        "description": "In-memory cache layer",
        "owner_division": "DIV-002",
        "status": "degraded",
        "last_deploy": "2026-05-19 13:16:49",
        "version": "v5.6.17",
    },
    {
        "id": "SVC-008",
        "name": "postgres-primary",
        "description": "Primary relational database",
        "owner_division": "DIV-002",
        "status": "degraded",
        "last_deploy": "2026-05-21 13:16:49",
        "version": "v3.3.5",
    },
    {
        "id": "SVC-019",
        "name": "vpn-service",
        "description": "Corporate VPN gateway",
        "owner_division": "DIV-002",
        "status": "healthy",
        "last_deploy": "2026-05-04 13:16:49",
        "version": "v5.4.18",
    },
]

_DEFAULT_METRICS = [
    {
        "service_id": "auth-api",
        "cpu_percent": 32.5,
        "memory_percent": 54.0,
        "error_rate_percent": 0.05,
        "p95_latency_ms": 120.0,
        "status": "healthy",
        "recorded_at": datetime.now().isoformat(),
    },
    {
        "service_id": "redis-cluster",
        "cpu_percent": 94.2,
        "memory_percent": 98.4,
        "error_rate_percent": 14.50,
        "p95_latency_ms": 1420.0,
        "status": "degraded",
        "recorded_at": datetime.now().isoformat(),
    },
    {
        "service_id": "postgres-primary",
        "cpu_percent": 89.1,
        "memory_percent": 88.5,
        "error_rate_percent": 6.70,
        "p95_latency_ms": 780.0,
        "status": "degraded",
        "recorded_at": datetime.now().isoformat(),
    },
    {
        "service_id": "checkout-api",
        "cpu_percent": 72.3,
        "memory_percent": 76.8,
        "error_rate_percent": 5.80,
        "p95_latency_ms": 980.0,
        "status": "degraded",
        "recorded_at": datetime.now().isoformat(),
    },
    {
        "service_id": "vpn-service",
        "cpu_percent": 24.5,
        "memory_percent": 45.1,
        "error_rate_percent": 0.02,
        "p95_latency_ms": 38.0,
        "status": "healthy",
        "recorded_at": datetime.now().isoformat(),
    },
]

_DEFAULT_TICKETS = [
    {
        "id": "TKT-00001",
        "reported_by": "EMP-0408",
        "issue_description": "MFA token not working",
        "ticket_type": "access_identity",
        "severity": "medium",
        "status": "resolved",
        "assigned_to": "EMP-0209",
        "created_at": "2026-05-09 01:16:49",
        "resolved_at": "2026-05-09 01:39:49",
        "resolution_notes": "Resynced TOTP seed",
    },
    {
        "id": "TKT-00002",
        "reported_by": "EMP-0098",
        "issue_description": "SSO not working on staging",
        "ticket_type": "access_identity",
        "severity": "medium",
        "status": "open",
        "assigned_to": None,
        "created_at": "2026-05-24 12:16:49",
        "resolved_at": None,
        "resolution_notes": None,
    },
    {
        "id": "TKT-00021",
        "reported_by": "EMP-0609",
        "issue_description": "Cannot connect to VPN client gateway",
        "ticket_type": "access_identity",
        "severity": "low",
        "status": "investigating",
        "assigned_to": "EMP-0502",
        "created_at": "2026-05-06 13:16:49",
        "resolved_at": None,
        "resolution_notes": None,
    },
]

_DEFAULT_ASSETS = [
    {
        "id": "AST-0001",
        "name": "SW-core-9",
        "asset_type": "network_switch",
        "status": "healthy",
        "owner_id": "EMP-0291",
        "cpu_usage_percent": None,
        "memory_usage_percent": None,
        "location": "us-west-2",
        "last_health_check": "2026-06-03 01:33:49",
    },
    {
        "id": "AST-0003",
        "name": "LP-Surface-3740",
        "asset_type": "laptop",
        "status": "healthy",
        "owner_id": "EMP-0216",
        "cpu_usage_percent": None,
        "memory_usage_percent": None,
        "location": "ap-south-1",
        "last_health_check": "2026-06-03 11:24:49",
    },
    {
        "id": "AST-0014",
        "name": "LP-MBP-2965",
        "asset_type": "laptop",
        "status": "offline",
        "owner_id": "EMP-0089",
        "cpu_usage_percent": None,
        "memory_usage_percent": None,
        "location": "us-east-1",
        "last_health_check": "2026-06-03 00:26:49",
    },
]

_DEFAULT_CLEARANCES = [
    {
        "user_id": "EMP-0012",
        "action_type": "restart_service",
        "granted": True,
        "approved_by": "EMP-0044",
    },
    {
        "user_id": "EMP-0012",
        "action_type": "clear_cache",
        "granted": True,
        "approved_by": "EMP-0044",
    },
    {
        "user_id": "EMP-0012",
        "action_type": "initiate_hardware_replacement",
        "granted": True,
        "approved_by": "EMP-0044",
    },
    {
        "user_id": "EMP-0044",
        "action_type": "restart_service",
        "granted": True,
        "approved_by": "SYSTEM",
    },
    {"user_id": "EMP-0044", "action_type": "clear_cache", "granted": True, "approved_by": "SYSTEM"},
    {
        "user_id": "EMP-0005",
        "action_type": "clear_cache",
        "granted": True,
        "approved_by": "EMP-0044",
    },
    {
        "user_id": "EMP-0005",
        "action_type": "restart_service",
        "granted": False,
        "approved_by": "EMP-0044",
    },
]


class DatabaseManager:
    """Manages queries with Postgres connection and automatic in-memory fallback."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or get_settings().database_url
        self._is_postgres_available: bool | None = None
        # In-memory mutable copies
        self.tickets: list[dict[str, Any]] = [dict(t) for t in _DEFAULT_TICKETS]
        self.services: list[dict[str, Any]] = [dict(s) for s in _DEFAULT_SERVICES]
        self.metrics: list[dict[str, Any]] = [dict(m) for m in _DEFAULT_METRICS]
        self.assets: list[dict[str, Any]] = [dict(a) for a in _DEFAULT_ASSETS]
        self.clearances: list[dict[str, Any]] = [dict(c) for c in _DEFAULT_CLEARANCES]
        self.audit_logs: list[dict[str, Any]] = []

    def is_live(self) -> bool:
        """Check if live PostgreSQL connection is usable."""
        if self._is_postgres_available is not None:
            return self._is_postgres_available

        if not self.dsn:
            self._is_postgres_available = False
            return False

        try:
            import psycopg

            with psycopg.connect(self.dsn, connect_timeout=2) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1;")
                    self._is_postgres_available = True
                    self._ensure_tables_exist(conn)
                    return True
        except Exception as e:  # noqa: BLE001
            log.info("Postgres not available (%s), using in-memory catalog.", e)
            self._is_postgres_available = False
            return False

    def _ensure_tables_exist(self, conn: Any) -> None:
        """Create Day 8 extension tables if not present in connected DB."""
        try:
            sql_file = ROOT / "db" / "init" / "02_observability_and_clearances.sql"
            if sql_file.exists():
                with conn.cursor() as cur:
                    cur.execute(sql_file.read_text(encoding="utf-8"))
                conn.commit()
        except Exception as e:  # noqa: BLE001
            log.warning("Could not execute 02_observability_and_clearances.sql: %s", e)

    # -------------------------------------------------------------------------
    # Ticketing Operations
    # -------------------------------------------------------------------------
    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        """Retrieve a ticket by ID."""
        if self.is_live():
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM live_tickets WHERE id = %s;", (ticket_id,))
                    row = cur.fetchone()
                    return dict(row) if row else None

        for t in self.tickets:
            if t["id"].lower() == ticket_id.lower():
                return dict(t)
        return None

    def search_tickets(
        self, query: str = "", status: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Search tickets by keyword in issue_description and optional status."""
        if self.is_live():
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    conditions = []
                    params: list[Any] = []
                    if query:
                        conditions.append("issue_description ILIKE %s")
                        params.append(f"%{query}%")
                    if status:
                        conditions.append("status = %s")
                        params.append(status)
                    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
                    params.append(limit)
                    cur.execute(
                        f"SELECT * FROM live_tickets {where} ORDER BY created_at DESC LIMIT %s;",
                        params,
                    )
                    return [dict(r) for r in cur.fetchall()]

        results = []
        for t in self.tickets:
            if query and query.lower() not in t["issue_description"].lower():
                continue
            if status and t["status"].lower() != status.lower():
                continue
            results.append(dict(t))
            if len(results) >= limit:
                break
        return results

    def create_ticket(
        self,
        reported_by: str,
        issue_description: str,
        ticket_type: str,
        severity: str = "medium",
    ) -> dict[str, Any]:
        """Create a new ticket and persist it."""
        ticket_id = f"TKT-{datetime.now().strftime('%Y%m%d%H%M%S')[-8:]}"
        now = datetime.now().isoformat()

        if self.is_live():
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO live_tickets (id, reported_by, issue_description, ticket_type, severity, status, created_at)
                        VALUES (%s, %s, %s, %s, %s, 'open', NOW())
                        RETURNING *;
                        """,
                        (ticket_id, reported_by, issue_description, ticket_type, severity),
                    )
                    row = cur.fetchone()
                    conn.commit()
                    return dict(row) if row else {}

        record = {
            "id": ticket_id,
            "reported_by": reported_by,
            "issue_description": issue_description,
            "ticket_type": ticket_type,
            "severity": severity,
            "status": "open",
            "assigned_to": None,
            "created_at": now,
            "resolved_at": None,
            "resolution_notes": None,
        }
        self.tickets.append(record)
        return dict(record)

    def update_ticket(
        self,
        ticket_id: str,
        status: str | None = None,
        resolution_notes: str | None = None,
        assigned_to: str | None = None,
    ) -> dict[str, Any] | None:
        """Update an existing ticket."""
        if self.is_live():
            import psycopg
            from psycopg.rows import dict_row

            updates = []
            params: list[Any] = []
            if status:
                updates.append("status = %s")
                params.append(status)
                if status == "resolved":
                    updates.append("resolved_at = NOW()")
            if resolution_notes:
                updates.append("resolution_notes = %s")
                params.append(resolution_notes)
            if assigned_to:
                updates.append("assigned_to = %s")
                params.append(assigned_to)

            if not updates:
                return self.get_ticket(ticket_id)

            params.append(ticket_id)
            with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE live_tickets SET {', '.join(updates)} WHERE id = %s RETURNING *;",
                        params,
                    )
                    row = cur.fetchone()
                    conn.commit()
                    return dict(row) if row else None

        for t in self.tickets:
            if t["id"].lower() == ticket_id.lower():
                if status:
                    t["status"] = status
                    if status == "resolved":
                        t["resolved_at"] = datetime.now().isoformat()
                if resolution_notes:
                    t["resolution_notes"] = resolution_notes
                if assigned_to:
                    t["assigned_to"] = assigned_to
                return dict(t)
        return None

    def lookup_asset(
        self,
        asset_id: str | None = None,
        owner_id: str | None = None,
        name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Look up hardware assets in assets_inventory."""
        if self.is_live():
            import psycopg
            from psycopg.rows import dict_row

            conditions = []
            params: list[Any] = []
            if asset_id:
                conditions.append("id = %s")
                params.append(asset_id)
            if owner_id:
                conditions.append("owner_id = %s")
                params.append(owner_id)
            if name:
                conditions.append("name ILIKE %s")
                params.append(f"%{name}%")
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT * FROM assets_inventory {where} LIMIT 20;", params)
                    return [dict(r) for r in cur.fetchall()]

        results = []
        for a in self.assets:
            if asset_id and a["id"].lower() != asset_id.lower():
                continue
            if owner_id and a["owner_id"].lower() != owner_id.lower():
                continue
            if name and name.lower() not in a["name"].lower():
                continue
            results.append(dict(a))
        return results

    def check_clearance(self, user_id: str, action_type: str) -> dict[str, Any]:
        """Verify whether an employee has clearance for a restricted action."""
        if self.is_live():
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    # 1. Check user_clearances table
                    cur.execute(
                        "SELECT granted, approved_by FROM user_clearances WHERE user_id = %s AND action_type = %s;",
                        (user_id, action_type),
                    )
                    row = cur.fetchone()
                    if row:
                        return {
                            "user_id": user_id,
                            "action_type": action_type,
                            "cleared": bool(row["granted"]),
                            "reason": f"Explicit clearance policy (approved_by: {row['approved_by']})",
                        }

                    # 2. Check employee role & clearance level (clearance >= 4 or DevOps division = DIV-002)
                    cur.execute(
                        "SELECT name, role, division_id, clearance_level FROM employees WHERE id = %s;",
                        (user_id,),
                    )
                    emp = cur.fetchone()
                    if emp:
                        if emp.get("division_id") == "DIV-002" or (
                            emp.get("clearance_level") and emp["clearance_level"] >= 4
                        ):
                            return {
                                "user_id": user_id,
                                "action_type": action_type,
                                "cleared": True,
                                "reason": f"DevOps role / clearance level {emp.get('clearance_level')}",
                            }
                        return {
                            "user_id": user_id,
                            "action_type": action_type,
                            "cleared": False,
                            "reason": f"Insufficient clearance level ({emp.get('clearance_level')}) for action '{action_type}'",
                        }

        # In-memory check
        for c in self.clearances:
            if c["user_id"].lower() == user_id.lower() and c["action_type"] == action_type:
                return {
                    "user_id": user_id,
                    "action_type": action_type,
                    "cleared": bool(c["granted"]),
                    "reason": f"Explicit clearance policy (approved_by: {c.get('approved_by')})",
                }

        # Default fallback by user ID conventions
        if user_id.upper() in ("EMP-0012", "EMP-0044", "EMP-0056"):  # Known DevOps
            return {
                "user_id": user_id,
                "action_type": action_type,
                "cleared": True,
                "reason": "DevOps role clearance verified",
            }

        return {
            "user_id": user_id,
            "action_type": action_type,
            "cleared": False,
            "reason": f"User {user_id} lacks clearance for restricted action '{action_type}'",
        }

    # -------------------------------------------------------------------------
    # Observability Operations
    # -------------------------------------------------------------------------
    def get_service_info(self, service: str) -> dict[str, Any] | None:
        """Fetch metadata, owner, version, and status for a service."""
        if self.is_live():
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT s.*, d.name AS division_name 
                        FROM services s 
                        LEFT JOIN divisions d ON s.owner_division = d.id 
                        WHERE s.name ILIKE %s OR s.id = %s;
                        """,
                        (service, service),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None

        for s in self.services:
            if s["name"].lower() == service.lower() or s["id"].lower() == service.lower():
                return dict(s)
        return None

    def get_metrics(self, service: str, window_minutes: int = 60) -> dict[str, Any]:
        """Aggregate telemetry metrics (avg/max CPU, memory, error rates, p95)."""
        clean_name = service.lower().strip()
        data_points = []

        if self.is_live():
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT * FROM server_metrics 
                        WHERE service_id ILIKE %s 
                        ORDER BY recorded_at DESC LIMIT 20;
                        """,
                        (f"%{clean_name}%",),
                    )
                    data_points = [dict(r) for r in cur.fetchall()]

        if not data_points:
            data_points = [
                dict(m) for m in self.metrics if clean_name in m["service_id"].lower()
            ]

        if not data_points:
            # Generate baseline healthy metrics if service is unknown
            data_points = [
                {
                    "service_id": service,
                    "cpu_percent": 25.0,
                    "memory_percent": 45.0,
                    "error_rate_percent": 0.01,
                    "p95_latency_ms": 40.0,
                    "status": "healthy",
                    "recorded_at": datetime.now().isoformat(),
                }
            ]

        cpus = [p["cpu_percent"] for p in data_points]
        mems = [p["memory_percent"] for p in data_points]
        errors = [p["error_rate_percent"] for p in data_points]
        lats = [p["p95_latency_ms"] for p in data_points]

        return {
            "service": service,
            "window_minutes": window_minutes,
            "sample_count": len(data_points),
            "summary": {
                "avg_cpu_percent": round(sum(cpus) / len(cpus), 2),
                "max_cpu_percent": max(cpus),
                "avg_memory_percent": round(sum(mems) / len(mems), 2),
                "max_memory_percent": max(mems),
                "avg_error_rate_percent": round(sum(errors) / len(errors), 2),
                "max_error_rate_percent": max(errors),
                "avg_p95_latency_ms": round(sum(lats) / len(lats), 2),
                "max_p95_latency_ms": max(lats),
                "current_status": data_points[0].get("status", "healthy"),
            },
            "recent_samples": data_points[:5],
        }

    def list_anomalies(
        self, service: str | None = None, window_minutes: int = 60
    ) -> list[dict[str, Any]]:
        """Identify services with elevated CPU, memory, error spikes, or degraded status."""
        anomalies = []
        services_to_check = (
            [service] if service else [s["name"] for s in self.services]
        )

        for s_name in services_to_check:
            metrics = self.get_metrics(s_name, window_minutes=window_minutes)
            summ = metrics["summary"]
            reasons = []

            if summ["max_memory_percent"] >= 90.0:
                reasons.append(f"Memory exhaustion critical ({summ['max_memory_percent']}%)")
            elif summ["avg_memory_percent"] >= 80.0:
                reasons.append(f"High memory utilization ({summ['avg_memory_percent']}%)")

            if summ["max_cpu_percent"] >= 85.0:
                reasons.append(f"CPU spike detected ({summ['max_cpu_percent']}%)")

            if summ["max_error_rate_percent"] >= 5.0:
                reasons.append(f"Elevated error rate ({summ['max_error_rate_percent']}%)")

            if summ["current_status"] in ("degraded", "offline"):
                reasons.append(f"Reported status: {summ['current_status']}")

            if reasons:
                anomalies.append(
                    {
                        "service": s_name,
                        "status": summ["current_status"],
                        "reasons": reasons,
                        "metrics": summ,
                    }
                )

        return anomalies

    # -------------------------------------------------------------------------
    # Action & Audit Operations (Day 9)
    # -------------------------------------------------------------------------
    def log_action(
        self,
        action_type: str,
        target_resource: str,
        requested_by: str,
        dry_run: bool,
        clearance_granted: bool,
        status: str,
        details: str = "",
    ) -> dict[str, Any]:
        """Record an administrative action execution in the audit trail."""
        now = datetime.now().isoformat()
        record = {
            "action_type": action_type,
            "target_resource": target_resource,
            "requested_by": requested_by,
            "dry_run": dry_run,
            "clearance_granted": clearance_granted,
            "status": status,
            "details": details,
            "executed_at": now,
        }

        if self.is_live():
            import psycopg
            from psycopg.rows import dict_row

            try:
                with psycopg.connect(self.dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO action_audit_log (action_type, target_resource, requested_by, dry_run, clearance_granted, status, details, executed_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                        RETURNING *;
                        """,
                        (action_type, target_resource, requested_by, dry_run, clearance_granted, status, details),
                    )
                    row = cur.fetchone()
                    conn.commit()
                    return dict(row) if row else record
            except Exception as e:  # noqa: BLE001
                log.warning("Could not write audit log to PostgreSQL: %s", e)

        self.audit_logs.append(record)
        return record

    def get_audit_logs(
        self, target_resource: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Fetch historical audit log entries."""
        if self.is_live():
            import psycopg
            from psycopg.rows import dict_row

            try:
                with psycopg.connect(self.dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
                    if target_resource:
                        cur.execute(
                            "SELECT * FROM action_audit_log WHERE target_resource ILIKE %s ORDER BY executed_at DESC LIMIT %s;",
                            (f"%{target_resource}%", limit),
                        )
                    else:
                        cur.execute(
                            "SELECT * FROM action_audit_log ORDER BY executed_at DESC LIMIT %s;",
                            (limit,),
                        )
                    return [dict(r) for r in cur.fetchall()]
            except Exception as e:  # noqa: BLE001
                log.warning("Could not read audit logs from PostgreSQL: %s", e)

        results = []
        for a in reversed(self.audit_logs):
            if target_resource and target_resource.lower() not in a["target_resource"].lower():
                continue
            results.append(dict(a))
            if len(results) >= limit:
                break
        return results

    def restart_service_record(self, service: str) -> bool:
        """Simulate service restart: sets status to 'healthy' in state/DB."""
        if self.is_live():
            import psycopg

            try:
                with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE services SET status = 'healthy', last_deploy = NOW() WHERE name ILIKE %s;",
                        (service,),
                    )
                    conn.commit()
            except Exception as e:  # noqa: BLE001
                log.warning("Could not update service status in PostgreSQL: %s", e)

        for s in self.services:
            if s["name"].lower() == service.lower():
                s["status"] = "healthy"
                s["last_deploy"] = datetime.now().isoformat()
                return True
        return False

    def clear_cache_record(self, service: str) -> bool:
        """Simulate cache clear: relieves memory pressure in state/DB."""
        for m in self.metrics:
            if service.lower() in m["service_id"].lower():
                m["memory_percent"] = 42.0
                m["cpu_percent"] = 35.0
                m["error_rate_percent"] = 0.05
                m["status"] = "healthy"
                return True
        return False


# Global singleton instance
_db_manager: DatabaseManager | None = None


def get_db_manager() -> DatabaseManager:
    """Return the global DatabaseManager singleton."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager
