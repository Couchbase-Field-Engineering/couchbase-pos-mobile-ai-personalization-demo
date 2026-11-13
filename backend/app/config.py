"""Runtime configuration, all from environment variables (see .env.example)."""
import os


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# --- storage -----------------------------------------------------------------
# "couchbase" for the full stack, "memory" for a no-dependency local run.
STORE_DRIVER = os.getenv("STORE_DRIVER", "memory")
COUCHBASE_CONN = os.getenv("COUCHBASE_CONN", "couchbase://couchbase")
COUCHBASE_USERNAME = os.getenv("COUCHBASE_USERNAME", "Administrator")
COUCHBASE_PASSWORD = os.getenv("COUCHBASE_PASSWORD", "password123")
COUCHBASE_BUCKET = os.getenv("COUCHBASE_BUCKET", "pos")
COUCHBASE_SCOPE = os.getenv("COUCHBASE_SCOPE", "retail")

# --- Databricks dump ------------------------------------------------------------
DUMP_DIR = os.getenv("DUMP_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "data", "databricks-dump"))
# How often the backend checks the dump folder for a newer dt= partition.
DUMP_POLL_SECONDS = int(os.getenv("DUMP_POLL_SECONDS", "600"))
# Memory driver only: keep the first N shoppers of the export (the demo personas are always its first rows).
MEMORY_SHOPPER_LIMIT = int(os.getenv("MEMORY_SHOPPER_LIMIT", "1500"))
# Dynamic pricing: the rules are rebuilt on every inventory change and on this timer.
PRICING_REFRESH_SECONDS = int(os.getenv("PRICING_REFRESH_SECONDS", "60"))
# Time-window promotions use the wall clock in this zone, unless DEMO_HOUR pins the hour (0-23).
PRICING_TZ = os.getenv("PRICING_TZ", "America/New_York")
DEMO_HOUR = int(os.environ["DEMO_HOUR"]) if os.getenv("DEMO_HOUR", "").strip() else None
# Recommendations are pre-built for shoppers who opened the app within this many
# days (and for the demo personas); everyone else gets theirs at sign-in.
RECS_WINDOW_DAYS = int(os.getenv("RECS_WINDOW_DAYS", "1"))
# "Today" for the demo. Empty = the export_date of the loaded dump, so the
# story (days since last order, what is about to run out) stays the same whenever it's run.
DEMO_TODAY = os.getenv("DEMO_TODAY", "")

# --- Knowledge Base publishing -------------------------------------------------
KB_ENABLED = _bool("KB_ENABLED", True)
# Shopper profiles are published for shoppers active within this many days.
KB_WINDOW_DAYS = int(os.getenv("KB_WINDOW_DAYS", "2"))
KB_PROFILE_LIMIT = int(os.getenv("KB_PROFILE_LIMIT", "150"))
KB_ALLOWED_ROLES = [r.strip() for r in os.getenv("KB_ALLOWED_ROLES", "sales_agent,admin").split(",") if r.strip()]

# --- Couchbase Agent Operations Manager ------------------------------------------
AOM_URL = os.getenv("AOM_URL", "").rstrip("/")
AOM_AGENT_API_KEY = os.getenv("AOM_AGENT_API_KEY", "")
# The store agents have their own AOM identities (see agents.py). Without a key
# of their own they act under the assistant's.
AOM_VOICE_AGENT_API_KEY = os.getenv("AOM_VOICE_AGENT_API_KEY", "")
AOM_VISION_AGENT_API_KEY = os.getenv("AOM_VISION_AGENT_API_KEY", "")
AOM_ADMIN_USERNAME = os.getenv("AOM_ADMIN_USERNAME", "admin")
AOM_ADMIN_PASSWORD = os.getenv("AOM_ADMIN_PASSWORD", "")
AOM_VERIFY_TLS = _bool("AOM_VERIFY_TLS", False)
# Register with AOM on startup (MCP server, agent identity, approval tier, RAG
# app) instead of running scripts/register_with_aom.py - see aom_bootstrap.py.
# Needs AOM_URL and AOM_ADMIN_PASSWORD; used by the Helm chart.
AOM_AUTO_REGISTER = _bool("AOM_AUTO_REGISTER", False)
# The AOM dashboard as a browser reaches it - linked from the story page.
AOM_DASHBOARD_URL = os.getenv("AOM_DASHBOARD_URL", "")
AOM_TIMEOUT_SECONDS = float(os.getenv("AOM_TIMEOUT_SECONDS", "20"))
AOM_LLM_PROVIDER = os.getenv("AOM_LLM_PROVIDER", "") or None
AOM_LLM_MODEL = os.getenv("AOM_LLM_MODEL", "") or None
AOM_MCP_SERVER_ID = os.getenv("AOM_MCP_SERVER_ID", "shopsmart-pos")
# The URL AOM (in its own compose stack) uses to reach this backend's MCP server.
AOM_MCP_PUBLIC_URL = os.getenv("AOM_MCP_PUBLIC_URL", "http://host.docker.internal:8204/pos/mcp")
# The shopping assistant is registered in AOM as a RAG Application. AOM attributes
# cache activity to an app by namespace prefix `rag:<app_id>:`, so every
# namespace the assistant uses (its own context cache and LLM calls as well as
# the app's /query endpoint) carries that prefix and shows up on AOM's RAG
# Applications page.
RAG_APP_ID = os.getenv("RAG_APP_ID", "shopsmart-pos-assistant")
RAG_APP_ENABLED = os.getenv("RAG_APP_ENABLED", "true").lower() == "true"
LLM_NAMESPACE_SHOPPER = os.getenv("LLM_NAMESPACE_SHOPPER", f"rag:{RAG_APP_ID}:shopper")
LLM_NAMESPACE_GUIDE = os.getenv("LLM_NAMESPACE_GUIDE", f"rag:{RAG_APP_ID}:guide")
CONTEXT_NAMESPACE = os.getenv("CONTEXT_NAMESPACE", f"rag:{RAG_APP_ID}:shopper-context")

# --- Context cache -----------------------------------------------------------------
CONTEXT_CACHE_TTL_SECONDS = int(os.getenv("CONTEXT_CACHE_TTL_SECONDS", str(6 * 3600)))

# --- Sync Gateway (for Couchbase Lite replication) -----------------------------------
SG_ADMIN_URL = os.getenv("SG_ADMIN_URL", "").rstrip("/")  # e.g. http://sync-gateway:4985
SG_ADMIN_USERNAME = os.getenv("SG_ADMIN_USERNAME", "Administrator")
SG_ADMIN_PASSWORD = os.getenv("SG_ADMIN_PASSWORD", "password123")
SG_DB = os.getenv("SG_DB", "pos")
# What the phone dials. Defaults to blank: the app falls back to EXPO_PUBLIC_SG_URL.
SG_PUBLIC_URL = os.getenv("SG_PUBLIC_URL", "")
SG_SESSION_TTL_SECONDS = int(os.getenv("SG_SESSION_TTL_SECONDS", str(24 * 3600)))

# --- app --------------------------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET", "pos-demo-dev-secret-change-me")
# The app renews its token on every launch (/api/auth/refresh), so this is idle time.
APP_TOKEN_TTL_SECONDS = int(os.getenv("APP_TOKEN_TTL_SECONDS", str(30 * 24 * 3600)))
REQUEST_POLL_SECONDS = float(os.getenv("REQUEST_POLL_SECONDS", "2"))
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
