"""
Production incident scenarios. Each scenario contains:
- Realistic alert payloads, metrics, logs, recent deployments
- Diagnostic outputs (keyed by tool name)
- Ground truth: root_cause, correct_fix, wrong_fixes
- Runbook hints (optional guidance the agent may follow)

Difficulty:
  task_easy   — Redis OOM: clear single signal, no red herrings
  task_medium — PostgreSQL connection pool exhaustion: cascade with one red herring (API CPU)
  task_hard   — Auth service memory leak: deployment-triggered, multi-layer red herrings,
                 requires postmortem
"""
from __future__ import annotations

# ─── All available tools across every task ────────────────────────────────────

AVAILABLE_DIAGNOSTICS: list[str] = [
    # Logs & events
    "check_service_logs",
    "check_auth_service_logs",
    "check_payment_service_logs",
    # Memory & heap
    "check_service_memory_trend",
    "check_heap_profile",
    # CPU / network
    "check_cpu_usage",
    "check_network_latency",
    # Database
    "check_db_connections",
    "check_active_queries",
    "check_db_slow_query_log",
    # Cache
    "check_redis_memory",
    "check_redis_stats",
    # Deployment history
    "check_recent_deployments",
    "check_config_changes",
]

AVAILABLE_FIXES: list[str] = [
    # Redis
    "increase_redis_memory",
    "flush_redis_cache",
    "set_redis_eviction_policy",
    # Database
    "kill_long_running_query",
    "increase_connection_pool_size",
    "add_db_index",
    # Service lifecycle
    "restart_api_gateway",
    "restart_user_service",
    "restart_payment_service",
    "restart_auth_service",
    "rollback_auth_service",
    # Scaling
    "scale_up_api_gateway",
    "increase_payment_service_memory",
    "increase_auth_service_memory",
]

# ─── Scenario definitions ─────────────────────────────────────────────────────

SCENARIOS: dict[str, dict] = {
    # ══════════════════════════════════════════════════════════════════════════
    # TASK REAL — CPU SPIKE
    # Real metrics driven.
    # ══════════════════════════════════════════════════════════════════════════
    "task_real_cpu": {
        "id": "task_real_cpu",
        "name": "Live Real-World API Gateway CPU Spike",
        "description": "This task monitors your actual computer's CPU. Try running baseline/stress.py and watch the gateway CPU match it!",
        "difficulty": "medium",
        "root_cause": "real_world_cpu_stress",
        "root_service": "api-gateway",
        "affected_services": ["api-gateway"],
        "correct_diagnostics": ["check_cpu_usage"],
        "correct_fix": "scale_up_api_gateway",
        "wrong_fixes": ["restart_auth_service", "kill_long_running_query"],
        "requires_postmortem": False,
        "max_steps": 15,
        "initial_alerts": [
            {
                "alert_id": "ALT-REAL-1",
                "service": "api-gateway",
                "severity": "critical",
                "message": "Gateway CPU spike tracking host CPU.",
                "timestamp": "2025-03-28T02:14:00Z",
                "metric": "cpu_percent",
                "value": 100.0,
            }
        ],
        "service_metrics": {
            "api-gateway": {
                "service_name": "api-gateway",
                "cpu_percent": 0.0, # Will be replaced dynamically by psutil
                "memory_percent": 50.0,
                "error_rate": 0.0,
                "latency_p99_ms": 100.0,
                "status": "degraded",
                "request_rate": 5000.0,
            }
        },
        "logs": [
            {"timestamp": "0Z", "service": "api-gateway", "level": "WARNING", "message": "High CPU load detected!"}
        ],
        "recent_deployments": [],
        "runbook_hints": [],
        "diagnostic_outputs": {
            "check_cpu_usage": {
                "output": "API Gateway CPU is currently matching host CPU. Run scale_up_api_gateway or kill stress.py.",
                "reveals": ["real_world_cpu_stress"]
            }
        }
    },

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 1 — EASY
    # Redis OOM: single clear signal, no red herrings, no postmortem required.
    # A perfect agent needs: check_redis_memory (or check_redis_stats or logs)
    # → identify redis_oom → apply increase_redis_memory → close.
    # ══════════════════════════════════════════════════════════════════════════
    "task_easy": {
        "id": "task_easy",
        "name": "Redis Cache Out-of-Memory",
        "description": (
            "The production Redis cache (maxmemory 2 GB, policy noeviction) has hit its memory "
            "limit. Write commands are rejected, causing a 10× surge in direct PostgreSQL reads "
            "and elevated API latency. The fix is straightforward — increase the maxmemory limit "
            "or switch eviction policy."
        ),
        "difficulty": "easy",
        "root_cause": "redis_oom",
        "root_service": "redis-cache",
        "affected_services": ["redis-cache", "api-gateway"],
        "correct_diagnostics": ["check_redis_memory", "check_redis_stats", "check_service_logs"],
        "correct_fix": "increase_redis_memory",
        "wrong_fixes": [
            "restart_api_gateway",
            "scale_up_api_gateway",
            "flush_redis_cache",
            "restart_user_service",
        ],
        "requires_postmortem": False,
        "max_steps": 12,

        # ── Monitoring state ──────────────────────────────────────────────────
        "initial_alerts": [
            {
                "alert_id": "ALT-001",
                "service": "redis-cache",
                "severity": "critical",
                "message": "Redis memory usage 99.8 % — OOM killer active; write commands rejected",
                "timestamp": "2025-03-28T02:14:00Z",
                "metric": "memory_percent",
                "value": 99.8,
            },
            {
                "alert_id": "ALT-002",
                "service": "api-gateway",
                "severity": "warning",
                "message": "Cache hit-rate fell from 87 % to 4 % — DB read surge in progress",
                "timestamp": "2025-03-28T02:14:00Z",
                "metric": "cache_hit_rate",
                "value": 4.0,
            },
        ],

        "service_metrics": {
            "redis-cache": {
                "service_name": "redis-cache",
                "cpu_percent": 12.3,
                "memory_percent": 99.8,
                "error_rate": 342.0,
                "latency_p99_ms": 0.9,
                "status": "degraded",
                "request_rate": 8200.0,
            },
            "api-gateway": {
                "service_name": "api-gateway",
                "cpu_percent": 41.2,
                "memory_percent": 55.1,
                "error_rate": 28.4,
                "latency_p99_ms": 1840.0,
                "status": "degraded",
                "request_rate": 3100.0,
            },
            "postgres-db": {
                "service_name": "postgres-db",
                "cpu_percent": 78.9,
                "memory_percent": 62.3,
                "error_rate": 0.2,
                "latency_p99_ms": 320.0,
                "status": "healthy",
                "request_rate": 7900.0,
            },
        },

        "logs": [
            {
                "timestamp": "2025-03-28T02:10:12Z",
                "service": "redis-cache",
                "level": "CRITICAL",
                "message": (
                    "OOM command not allowed when used memory > 'maxmemory'. "
                    "used_memory=2048MB  maxmemory=2048MB"
                ),
            },
            {
                "timestamp": "2025-03-28T02:10:15Z",
                "service": "redis-cache",
                "level": "ERROR",
                "message": (
                    "MISCONF Redis configured to save RDB snapshots but cannot persist on disk. "
                    "Write commands disabled."
                ),
            },
            {
                "timestamp": "2025-03-28T02:11:01Z",
                "service": "api-gateway",
                "level": "ERROR",
                "message": "Cache SET failed: OOM — falling back to direct PostgreSQL read",
            },
            {
                "timestamp": "2025-03-28T02:11:03Z",
                "service": "api-gateway",
                "level": "WARNING",
                "message": "Cache miss-rate 96 % — nearly all requests bypassing cache",
            },
            {
                "timestamp": "2025-03-28T02:13:44Z",
                "service": "postgres-db",
                "level": "WARNING",
                "message": "Connection pool at 89 % — cache fallback increasing direct-DB traffic",
            },
        ],

        "recent_deployments": [],

        "runbook_hints": [
            {
                "alert_pattern": "Redis memory_percent > 95",
                "hint": "Check Redis maxmemory config and eviction policy. If policy is noeviction, writes are rejected when limit is hit.",
                "suggested_diagnostics": ["check_redis_memory", "check_redis_stats"],
            }
        ],

        # ── Diagnostic outputs (what each tool returns) ───────────────────────
        "diagnostic_outputs": {
            "check_redis_memory": {
                "output": (
                    "=== Redis INFO memory ===\n"
                    "  used_memory_human       : 2.00G\n"
                    "  maxmemory_human         : 2.00G   ← AT LIMIT\n"
                    "  mem_fragmentation_ratio : 1.02\n"
                    "  maxmemory_policy        : noeviction  ← writes rejected when full\n"
                    "  evicted_keys            : 0\n"
                    "  rejected_commands       : 98,432\n\n"
                    "CONCLUSION: Redis is at 100 % of its 2 GB limit with noeviction policy. "
                    "All SET/LPUSH/ZADD commands are being rejected. Increasing maxmemory will restore writes."
                ),
                "reveals": ["redis_oom"],
            },
            "check_redis_stats": {
                "output": (
                    "=== Redis INFO stats ===\n"
                    "  connected_clients : 214\n"
                    "  rejected_connections : 0\n"
                    "  keyspace_hits    : 312,401\n"
                    "  keyspace_misses  : 7,840,220   ← 96 % miss rate — cache not serving writes\n"
                    "  evicted_keys     : 0\n"
                    "  expired_keys     : 0\n\n"
                    "The miss rate explosion correlates with the OOM event. Redis cannot accept new cache entries."
                ),
                "reveals": ["redis_oom"],
            },
            "check_service_logs": {
                "output": (
                    "Last 5 redis-cache ERROR/CRITICAL lines:\n"
                    "  [CRITICAL] OOM command not allowed (×98,432 times in 4 min)\n"
                    "  [ERROR]    MISCONF RDB snapshot cannot write to disk\n\n"
                    "Root cause evident: memory exhaustion with noeviction policy."
                ),
                "reveals": ["redis_oom"],
            },
            "check_network_latency": {
                "output": "Network latency nominal (p99=0.3 ms). No packet loss. Not a contributing factor.",
                "reveals": [],
            },
            "check_cpu_usage": {
                "output": "CPU within normal bounds for all services. Not a contributing factor.",
                "reveals": [],
            },
            "check_db_connections": {
                "output": (
                    "PostgreSQL connections: 172/200 (elevated due to cache fallback). "
                    "This is a symptom of the Redis OOM, not the root cause."
                ),
                "reveals": [],
            },
            "check_recent_deployments": {
                "output": "No deployments in the last 24 h. Incident likely caused by organic cache growth.",
                "reveals": [],
            },
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 2 — MEDIUM
    # PostgreSQL connection pool exhausted by an analytics query run against prod.
    # Red herring: API gateway CPU spike (symptom of retry storm, not root cause).
    # Correct path: check_db_connections → check_active_queries → kill_long_running_query
    # ══════════════════════════════════════════════════════════════════════════
    "task_medium": {
        "id": "task_medium",
        "name": "Database Connection Pool Exhaustion",
        "description": (
            "A data analyst accidentally ran a full-table-scan analytics query against the "
            "production PostgreSQL primary. The query has held an idle-in-transaction connection "
            "for 26 minutes, slowly consuming all 200 connection slots. Downstream services "
            "(user-service, order-service) hit SQLSTATE 53300 errors. The API gateway shows "
            "high CPU because of a retry storm — this is a *secondary* effect, not the root cause."
        ),
        "difficulty": "medium",
        "root_cause": "db_connection_pool_exhausted",
        "root_service": "postgres-db",
        "affected_services": ["postgres-db", "user-service", "order-service", "api-gateway"],
        "correct_diagnostics": [
            "check_db_connections",
            "check_active_queries",
            "check_service_logs",
            "check_db_slow_query_log",
        ],
        "correct_fix": "kill_long_running_query",
        "wrong_fixes": [
            "restart_api_gateway",
            "scale_up_api_gateway",
            "increase_redis_memory",
            "restart_user_service",
            "increase_connection_pool_size",
        ],
        "requires_postmortem": False,
        "max_steps": 18,

        "initial_alerts": [
            {
                "alert_id": "ALT-101",
                "service": "user-service",
                "severity": "critical",
                "message": "DB connection timeout — 95 % of login/profile requests failing (SQLSTATE 53300)",
                "timestamp": "2025-03-28T02:14:00Z",
                "metric": "error_rate",
                "value": 94.2,
            },
            {
                "alert_id": "ALT-102",
                "service": "order-service",
                "severity": "critical",
                "message": "DB connection timeout — order creation failing (SQLSTATE 53300)",
                "timestamp": "2025-03-28T02:14:00Z",
                "metric": "error_rate",
                "value": 87.1,
            },
            {
                "alert_id": "ALT-103",
                "service": "api-gateway",
                "severity": "warning",
                "message": "CPU at 91 % — retry storm from downstream 503s (secondary effect)",
                "timestamp": "2025-03-28T02:14:00Z",
                "metric": "cpu_percent",
                "value": 91.0,
            },
            {
                "alert_id": "ALT-104",
                "service": "postgres-db",
                "severity": "critical",
                "message": "max_connections (200) reached — FATAL: sorry, too many clients already",
                "timestamp": "2025-03-28T02:14:00Z",
                "metric": "active_connections",
                "value": 200.0,
            },
        ],

        "service_metrics": {
            "postgres-db": {
                "service_name": "postgres-db",
                "cpu_percent": 99.1,
                "memory_percent": 71.4,
                "error_rate": 180.0,
                "latency_p99_ms": 30000.0,
                "status": "degraded",
                "request_rate": 200.0,
                "open_connections": 200,
            },
            "user-service": {
                "service_name": "user-service",
                "cpu_percent": 22.0,
                "memory_percent": 38.2,
                "error_rate": 94.2,
                "latency_p99_ms": 30000.0,
                "status": "degraded",
                "request_rate": 1200.0,
            },
            "order-service": {
                "service_name": "order-service",
                "cpu_percent": 18.5,
                "memory_percent": 41.1,
                "error_rate": 87.1,
                "latency_p99_ms": 30000.0,
                "status": "degraded",
                "request_rate": 890.0,
            },
            "api-gateway": {
                "service_name": "api-gateway",
                "cpu_percent": 91.0,
                "memory_percent": 58.3,
                "error_rate": 6.2,
                "latency_p99_ms": 31000.0,
                "status": "degraded",
                "request_rate": 4100.0,
            },
        },

        "logs": [
            {
                "timestamp": "2025-03-28T01:48:00Z",
                "service": "postgres-db",
                "level": "INFO",
                "message": (
                    "User 'analytics_bot' connected from 10.0.4.22 — "
                    "query: SELECT * FROM orders JOIN users ..."
                ),
            },
            {
                "timestamp": "2025-03-28T02:09:11Z",
                "service": "postgres-db",
                "level": "WARNING",
                "message": "Active connections approaching max_connections limit (185/200)",
            },
            {
                "timestamp": "2025-03-28T02:11:55Z",
                "service": "postgres-db",
                "level": "CRITICAL",
                "message": "max_connections reached (200/200) — FATAL: sorry, too many clients already",
            },
            {
                "timestamp": "2025-03-28T02:12:01Z",
                "service": "user-service",
                "level": "ERROR",
                "message": "SQLSTATE[53300]: Too many connections — could not connect to server",
            },
            {
                "timestamp": "2025-03-28T02:12:03Z",
                "service": "order-service",
                "level": "ERROR",
                "message": "SQLSTATE[53300]: Too many connections — order creation aborted",
            },
            {
                "timestamp": "2025-03-28T02:13:22Z",
                "service": "api-gateway",
                "level": "WARNING",
                "message": "Retry storm detected — upstream returning 503, exponential backoff active",
            },
        ],

        "recent_deployments": [],

        "runbook_hints": [
            {
                "alert_pattern": "PostgreSQL max_connections reached",
                "hint": "Check pg_stat_activity for long-running or idle-in-transaction connections before scaling connection pool.",
                "suggested_diagnostics": ["check_db_connections", "check_active_queries"],
            },
            {
                "alert_pattern": "API gateway high CPU with downstream 503s",
                "hint": "High API CPU during a DB incident is usually a retry amplification effect. Fix the DB first.",
                "suggested_diagnostics": ["check_db_connections"],
            },
        ],

        "diagnostic_outputs": {
            "check_db_connections": {
                "output": (
                    "=== pg_stat_activity summary ===\n"
                    "  Total connections : 200 / 200  ← LIMIT REACHED\n"
                    "  Active queries    : 198\n"
                    "  Idle              : 2\n"
                    "  Waiting for lock  : 0\n\n"
                    "  PID 28842 | state=active | duration=26 min 4 s | user=analytics_bot\n"
                    "  query: SELECT o.*, u.*, p.* FROM orders o\n"
                    "         JOIN users u ON o.user_id = u.id\n"
                    "         JOIN products p ON o.product_id = p.id\n"
                    "         WHERE o.created_at > '2020-01-01' ORDER BY o.total DESC\n"
                    "  plan: Seq Scan on orders (42 M rows) — no index used\n\n"
                    "CONCLUSION: PID 28842 (analytics_bot) has occupied its connection for 26 min "
                    "with a full-table sequential scan, saturating the pool."
                ),
                "reveals": ["db_connection_pool_exhausted", "long_running_query_pid_28842"],
            },
            "check_active_queries": {
                "output": (
                    "=== Long-running queries (> 60 s) ===\n"
                    "  PID 28842 | 26 min 04 s | analytics_bot | "
                    "SEQ SCAN orders + JOIN users + JOIN products (42 M rows)\n\n"
                    "This query should run against the analytics read-replica, not production. "
                    "Killing PID 28842 will free all 200 connection slots immediately."
                ),
                "reveals": ["db_connection_pool_exhausted", "long_running_query_pid_28842"],
            },
            "check_db_slow_query_log": {
                "output": (
                    "Slow query log (last 30 min):\n"
                    "  28842 | 1564 s | analytics_bot | SELECT … FROM orders … (Seq Scan)\n\n"
                    "All other queries < 500 ms. The analytics query is the sole outlier."
                ),
                "reveals": ["db_connection_pool_exhausted", "long_running_query_pid_28842"],
            },
            "check_service_logs": {
                "output": (
                    "Error pattern across user-service + order-service:\n"
                    "  SQLSTATE 53300 errors started at 02:11:55 Z, "
                    "matching postgres log of max_connections breach.\n"
                    "  API gateway CPU spike is a secondary retry-storm effect — fixing the DB will resolve it."
                ),
                "reveals": ["db_connection_pool_exhausted"],
            },
            "check_cpu_usage": {
                "output": (
                    "API Gateway CPU 91 % — caused by retry amplification from downstream 503s. "
                    "NOT the root cause. Resolving the DB connection issue will bring CPU back to normal."
                ),
                "reveals": [],
            },
            "check_network_latency": {
                "output": "Network latency nominal (p99 = 0.4 ms). Not a contributing factor.",
                "reveals": [],
            },
            "check_redis_memory": {
                "output": "Redis memory at 61 % — healthy. Not a contributing factor.",
                "reveals": [],
            },
            "check_recent_deployments": {
                "output": "No deployments in the last 24 h. Incident caused by ad-hoc analytics query.",
                "reveals": [],
            },
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 3 — HARD
    # Auth-service v2.4.1 introduced an unbounded JWTCache causing a memory leak.
    # Symptoms: payment failures (auth timeout), API CPU spike (retry storm).
    # Red herrings:
    #   (a) payment-service disk I/O alert (background audit flush — scheduled)
    #   (b) API gateway CPU spike (secondary retry amplification)
    # Agent must:
    #   - identify auth_memory_leak AND link it to v2.4.1 deployment
    #   - apply rollback_auth_service (NOT restart, NOT memory increase)
    #   - write a scored postmortem
    # ══════════════════════════════════════════════════════════════════════════
    "task_hard": {
        "id": "task_hard",
        "name": "Auth Service Memory Leak with Cascading Failures",
        "description": (
            "auth-service v2.4.1 (deployed 2h ago) introduced an unbounded LRU cache for JWT "
            "validation that never evicts tokens. Heap grows ~12 MB/min. JWT validation is now "
            "experiencing 14-second GC pauses, causing payment failures. "
            "Two red herrings are present: (1) payment-service shows elevated disk I/O — "
            "this is a scheduled audit log flush, unrelated; (2) api-gateway has high CPU — "
            "retry amplification from auth timeouts, not the root. "
            "The correct response is to rollback auth-service to v2.4.0 and write a postmortem."
        ),
        "difficulty": "hard",
        "root_cause": "auth_memory_leak",
        "root_service": "auth-service",
        "affected_services": ["auth-service", "payment-service", "api-gateway"],
        "correct_diagnostics": [
            "check_service_memory_trend",
            "check_recent_deployments",
            "check_auth_service_logs",
            "check_heap_profile",
        ],
        "correct_fix": "rollback_auth_service",
        "wrong_fixes": [
            "restart_payment_service",
            "restart_api_gateway",
            "restart_auth_service",
            "increase_payment_service_memory",
            "increase_auth_service_memory",
            "kill_long_running_query",
            "increase_redis_memory",
        ],
        "requires_postmortem": True,
        "max_steps": 22,

        "initial_alerts": [
            {
                "alert_id": "ALT-201",
                "service": "payment-service",
                "severity": "critical",
                "message": "78 % of payment transactions failing — JWT validation timeout from auth-service",
                "timestamp": "2025-03-28T02:14:00Z",
                "metric": "error_rate",
                "value": 78.3,
            },
            {
                "alert_id": "ALT-202",
                "service": "api-gateway",
                "severity": "warning",
                "message": "Auth endpoint p99 latency 14,200 ms — auth retries causing CPU spike",
                "timestamp": "2025-03-28T02:14:00Z",
                "metric": "latency_p99_ms",
                "value": 14200.0,
            },
            {
                "alert_id": "ALT-203",
                "service": "auth-service",
                "severity": "warning",
                "message": "Heap memory growing 12 MB/min — JWTCache entries not evicted",
                "timestamp": "2025-03-28T02:14:00Z",
                "metric": "memory_percent",
                "value": 82.4,
            },
            {
                "alert_id": "ALT-204",
                "service": "payment-service",
                "severity": "warning",
                "message": (
                    "Disk I/O elevated (94 MB/s) — NOTE: this is a scheduled background "
                    "audit-log flush, NOT incident-related"
                ),
                "timestamp": "2025-03-28T02:14:00Z",
                "metric": "disk_io_mbps",
                "value": 94.2,
            },
        ],

        "service_metrics": {
            "auth-service": {
                "service_name": "auth-service",
                "cpu_percent": 44.1,
                "memory_percent": 82.4,
                "error_rate": 18.9,
                "latency_p99_ms": 14200.0,
                "status": "degraded",
                "request_rate": 2100.0,
                "gc_pause_ms": 4200.0,
            },
            "payment-service": {
                "service_name": "payment-service",
                "cpu_percent": 31.2,
                "memory_percent": 48.1,
                "error_rate": 78.3,
                "latency_p99_ms": 15100.0,
                "status": "degraded",
                "request_rate": 890.0,
                "disk_io_mbps": 94.2,
            },
            "api-gateway": {
                "service_name": "api-gateway",
                "cpu_percent": 88.2,
                "memory_percent": 52.3,
                "error_rate": 8.4,
                "latency_p99_ms": 15400.0,
                "status": "degraded",
                "request_rate": 4300.0,
            },
            "postgres-db": {
                "service_name": "postgres-db",
                "cpu_percent": 31.2,
                "memory_percent": 58.4,
                "error_rate": 0.1,
                "latency_p99_ms": 42.0,
                "status": "healthy",
                "request_rate": 3800.0,
            },
        },

        "logs": [
            {
                "timestamp": "2025-03-28T00:02:00Z",
                "service": "auth-service",
                "level": "INFO",
                "message": "Deployment auth-service:v2.4.1 complete — JWTCache refactored (unbounded LRU)",
            },
            {
                "timestamp": "2025-03-28T01:20:00Z",
                "service": "auth-service",
                "level": "WARNING",
                "message": "Heap usage trending: 1.2 GB and rising — JWTCache not releasing entries",
            },
            {
                "timestamp": "2025-03-28T02:09:40Z",
                "service": "auth-service",
                "level": "ERROR",
                "message": "GC pause 4200 ms — heap nearly exhausted, JWT validation severely degraded",
            },
            {
                "timestamp": "2025-03-28T02:10:01Z",
                "service": "payment-service",
                "level": "ERROR",
                "message": "Auth validation timeout after 10 000 ms — cannot process transaction without JWT",
            },
            {
                "timestamp": "2025-03-28T02:10:04Z",
                "service": "payment-service",
                "level": "WARNING",
                "message": "Disk I/O alert — background audit-log flush running (scheduled, not incident-related)",
            },
            {
                "timestamp": "2025-03-28T02:13:00Z",
                "service": "api-gateway",
                "level": "WARNING",
                "message": (
                    "Auth retry amplification: 3 retries/req × 4300 rps = 17 200 auth calls/s — "
                    "CPU spike caused by retries, NOT a CPU issue in itself"
                ),
            },
        ],

        "recent_deployments": [
            {
                "timestamp": "2025-03-28T00:02:00Z",
                "service": "auth-service",
                "version": "v2.4.1",
                "previous_version": "v2.4.0",
                "author": "dev-carlos",
                "changelog": (
                    "Refactored JWTCache to use unbounded LRU for perf improvement. "
                    "Removed TTL and max-size constraints — REVIEW NEEDED"
                ),
            },
            {
                "timestamp": "2025-03-27T14:30:00Z",
                "service": "payment-service",
                "version": "v1.9.2",
                "previous_version": "v1.9.1",
                "author": "dev-priya",
                "changelog": "Added background audit-log flush worker (runs hourly)",
            },
        ],

        "runbook_hints": [
            {
                "alert_pattern": "Auth service memory growing linearly with GC pauses",
                "hint": "A linearly growing heap often indicates a cache or collection with no eviction. Check recent deployments that touched caching code.",
                "suggested_diagnostics": [
                    "check_service_memory_trend",
                    "check_recent_deployments",
                    "check_heap_profile",
                ],
            },
            {
                "alert_pattern": "Payment failures correlate with auth latency spike",
                "hint": "Payment service depends on auth for JWT validation. Investigate auth-service health before payment-service.",
                "suggested_diagnostics": ["check_auth_service_logs", "check_service_memory_trend"],
            },
        ],

        "diagnostic_outputs": {
            "check_service_memory_trend": {
                "output": (
                    "=== Memory trends (last 3 h) ===\n"
                    "  auth-service    : 22 % → 82 %  (+60 pp in 2 h 12 min) ← ANOMALOUS\n"
                    "  payment-service : 47 % → 48 %  (stable)\n"
                    "  api-gateway     : 51 % → 52 %  (stable)\n"
                    "  postgres-db     : 57 % → 58 %  (stable)\n\n"
                    "Auth-service memory growing ~12 MB/min. All other services stable.\n"
                    "Linear growth pattern consistent with an unbounded cache accumulation."
                ),
                "reveals": ["auth_memory_leak"],
            },
            "check_recent_deployments": {
                "output": (
                    "=== Deployments in last 24 h ===\n"
                    "  2025-03-28T00:02Z  auth-service v2.4.1 (prev: v2.4.0)  — dev-carlos\n"
                    "    changelog: 'Refactored JWTCache to use unbounded LRU — TTL and max-size REMOVED'\n\n"
                    "  2025-03-27T14:30Z  payment-service v1.9.2 (prev: v1.9.1) — dev-priya\n"
                    "    changelog: 'Added background audit-log flush worker (hourly)'\n\n"
                    "CORRELATION: auth-service deployed 2 h before incident start. "
                    "Removal of JWTCache eviction aligns with observed memory growth."
                ),
                "reveals": ["auth_memory_leak", "auth_v241_deployment"],
            },
            "check_auth_service_logs": {
                "output": (
                    "=== auth-service log analysis ===\n"
                    "  GC pause trend:\n"
                    "    01:00 Z  —  80 ms\n"
                    "    01:30 Z  — 420 ms\n"
                    "    02:00 Z  — 1800 ms\n"
                    "    02:09 Z  — 4200 ms  ← JWT validation severely degraded\n\n"
                    "  JWTCache size:\n"
                    "    00:30 Z  —  12,000 entries\n"
                    "    01:30 Z  —  78,000 entries\n"
                    "    02:09 Z  — 180,000 entries  ← no eviction ever triggered\n\n"
                    "  JWT validation p99:\n"
                    "    00:30 Z  —     12 ms\n"
                    "    01:30 Z  —    340 ms\n"
                    "    02:13 Z  — 14,200 ms\n\n"
                    "ROOT CAUSE CONFIRMED: JWTCache in v2.4.1 accumulates entries indefinitely. "
                    "Fix: rollback to v2.4.0 which had TTL=15 min + max_size=50,000."
                ),
                "reveals": ["auth_memory_leak", "auth_v241_deployment"],
            },
            "check_heap_profile": {
                "output": (
                    "=== Heap dump analysis — auth-service ===\n"
                    "  Total heap used        : 1.84 GB\n"
                    "  Top retained objects:\n"
                    "    com.example.auth.JWTCache          : 1.84 GB  (96.2 %)\n"
                    "    └─ java.util.LinkedHashMap$Entry   : 180,422 instances\n"
                    "         each entry ≈ 10.2 KB (JWT payload + metadata)\n\n"
                    "  GC root holding JWTCache             : static field Application.jwtCache\n\n"
                    "FIX RECOMMENDATION: rollback to v2.4.0 or hotfix by adding "
                    "maximumSize(50_000).expireAfterWrite(15, MINUTES) to the CacheBuilder."
                ),
                "reveals": ["auth_memory_leak", "auth_v241_deployment"],
            },
            "check_payment_service_logs": {
                "output": (
                    "=== payment-service log analysis ===\n"
                    "  Auth timeout errors: started 02:10:01 Z (secondary — caused by auth degradation)\n"
                    "  Disk I/O alerts    : scheduled audit-log flush (background job, every 60 min)\n\n"
                    "CONCLUSION: payment-service itself is healthy. "
                    "Fixing auth-service will resolve payment failures. Disk I/O is a red herring."
                ),
                "reveals": [],
            },
            "check_db_connections": {
                "output": "DB connections: 87/200 — healthy. Not a contributing factor.",
                "reveals": [],
            },
            "check_redis_memory": {
                "output": "Redis memory: 61 % — healthy. Not a contributing factor.",
                "reveals": [],
            },
            "check_cpu_usage": {
                "output": (
                    "API Gateway CPU 88 % — caused by auth retry amplification (3 retries/req × 4300 rps). "
                    "This is a secondary effect. Resolving auth-service will restore normal CPU."
                ),
                "reveals": [],
            },
            "check_network_latency": {
                "output": "Network latency nominal (p99 = 0.3 ms). Not a contributing factor.",
                "reveals": [],
            },
            "check_service_logs": {
                "output": (
                    "Cross-service log correlation:\n"
                    "  auth-service GC pauses increasing since 01:00 Z\n"
                    "  payment failures started at 02:10:01 Z (10 s after auth GC pause hit 4200 ms)\n"
                    "  api-gateway retries amplifying auth load\n"
                    "Causal chain: auth memory leak → GC pauses → JWT timeout → payment failures → retry storm"
                ),
                "reveals": ["auth_memory_leak"],
            },
        },
    },
}
