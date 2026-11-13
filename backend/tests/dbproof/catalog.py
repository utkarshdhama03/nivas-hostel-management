"""The table lists the design relies on (docs/03 §1, §6). test_schema.py checks these
lists against the real database, so they can't silently drift from the migration."""

# 44 tables with "org_id NOT NULL" -> standard tenant_isolation policy (D74 +1, schema cleanup -5).
TENANT_TABLES = [
    "organization_profiles",
    "academic_sessions",
    "academic_terms",
    "org_counters",
    "hostels",
    "buildings",
    "floors",
    "room_types",
    "rooms",
    "beds",
    "users",
    "sessions",
    "auth_tokens",
    "email_otps",
    "login_attempts",
    "staff_members",
    "role_assignments",
    "files",
    "students",
    "student_guardians",
    "student_documents",
    "bed_allocations",
    "fee_structures",
    "payment_submissions",
    "payments",
    "payment_reversals",
    "refund_requests",
    "ledger_entries",
    "ledger_allocations",
    "leave_requests",
    "complaint_categories",
    "complaints",
    "complaint_updates",
    "complaint_attachments",
    "mess_menus",
    "notifications",
    "notification_preferences",
    "announcements",
    "student_registration_requests",
]

# RLS with special policies (org_id nullable or no org_id).
# administrator_emails (D78): FORCEd RLS with one policy for the owner; the app reads it only through
# administrator_org(). It has org_id NOT NULL but is not a tenant table.
SPECIAL_RLS_TABLES = [
    "organizations",
    "jobs",
    "audit_logs",
    "administrator_emails",
    "student_emails",  # D80
    "student_request_emails",  # D80
]

# No org_id, no RLS: protected by privileges (hms_app / hms_worker get nothing).
PLATFORM_TABLES = ["platform_users", "platform_sessions", "platform_settings", "backup_runs"]

# Tables edited through PATCH -> optimistic locking (Q70).
EDITABLE_TABLES = [
    "platform_settings",
    "organizations",
    "organization_profiles",
    "academic_sessions",
    "academic_terms",
    "hostels",
    "buildings",
    "floors",
    "room_types",
    "rooms",
    "beds",
    "users",
    "staff_members",
    "students",
    "student_guardians",
    "fee_structures",
    "complaint_categories",
    "complaints",
    "mess_menus",
]

# Append-only tables: hms_app may not UPDATE/DELETE, and a trigger stops even the owner.
APPEND_ONLY_TABLES = [
    "ledger_entries",
    "payments",
    "payment_reversals",
    "complaint_updates",
    "audit_logs",
]

# The only tables the worker may DELETE from (purging expiring security data).
WORKER_DELETABLE = {
    "jobs",
    "login_attempts",
    "email_otps",
    "auth_tokens",
    "student_registration_requests",
}

# The Platform Owner's read window: table -> columns it may SELECT (docs/03 §6). None = all columns.
PLATFORM_WINDOW: dict[str, set[str] | None] = {
    "organizations": None,
    "organization_profiles": {"org_id", "legal_name", "contact_email", "contact_phone", "city", "state"},
    "hostels": {"id", "org_id", "is_active", "created_at"},
    "beds": {"id", "org_id", "hostel_id", "status"},
    "bed_allocations": {"id", "org_id", "hostel_id", "status", "start_date", "end_date"},
    "students": {"id", "org_id", "hostel_id", "status", "created_at", "exited_on"},
    "users": {"id", "org_id", "kind", "status", "created_at", "last_login_at"},
    "login_attempts": {"org_id", "succeeded", "attempted_at"},
    "jobs": {"id", "org_id", "kind", "priority", "status", "run_at", "attempts", "created_at", "finished_at"},
    "audit_logs": {
        "id",
        "org_id",
        "hostel_id",
        "occurred_at",
        "actor_type",
        "actor_user_id",
        "actor_platform_user_id",
        "actor_roles",
        "action",
        "entity_type",
        "entity_id",
        "changed_fields",
        "request_id",
    },
}
