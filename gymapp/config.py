"""Environment configuration loader and startup validator.

Every environment-specific value enters the project through this module. Nothing
in settings.py is a literal secret.

`validate()` runs as the last statement of settings.py. Django imports settings
before it serves anything, so a violated check raises ImproperlyConfigured and the
process refuses to start rather than starting in a weakened state.
"""
import ipaddress
import os
import re
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Names read by this module, so .env.example can be checked for completeness.
KNOWN_VARIABLES = [
    "DJANGO_SECRET_KEY",
    "DJANGO_DEBUG",
    "DJANGO_ALLOWED_HOSTS",
    "DATABASE_URL",
    "EMAIL_BACKEND",
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_HOST_USER",
    "EMAIL_HOST_PASSWORD",
    "EMAIL_USE_TLS",
    "DEFAULT_FROM_EMAIL",
    "ACCESS_TOKEN_LIFETIME_MINUTES",
    "REFRESH_TOKEN_LIFETIME_DAYS",
    "THROTTLE_LOGIN_PER_MINUTE",
    "THROTTLE_REGISTRATION_PER_MINUTE",
    "THROTTLE_PASSWORD_RESET_PER_MINUTE",
    "CORS_ALLOWED_ORIGINS",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "RAZORPAY_ACCOUNT_CURRENCY",
    "SAAS_TRIAL_DAYS",
    "SAAS_INVOICE_LEAD_DAYS",
    "RECOVERY_LLM_CLIENT",
    "RECOVERY_LLM_MODEL",
    "OPENAI_API_KEY",
    "RECOVERY_CHECKOUT_BASE_URL",
]

INSECURE_KEY_PREFIX = "django-insecure-"
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
CONSOLE_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Minimum requests per minute any throttle may be configured to, so a
# misconfigured zero cannot lock every user out of logging in.
THROTTLE_FLOOR = 5

_HOSTNAME_RE = re.compile(
    r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))*\.?$", re.IGNORECASE
)


def load_dotenv(path=None):
    """Read KEY=VALUE lines from .env into os.environ without overwriting.

    Deliberately hand-rolled rather than pulling in a dependency: the format this
    project needs is one line per variable, with # comments.
    """
    env_path = Path(path or (BASE_DIR / ".env"))
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        # Real environment variables win over the file.
        os.environ.setdefault(name, value)


# ============ TYPED READERS ============

def env_str(name, default=None):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def require(name):
    value = env_str(name)
    if value is None:
        raise ImproperlyConfigured(
            f"Required environment variable {name} is not set."
        )
    return value


def env_bool(name, default):
    value = env_str(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    value = env_str(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"Environment variable {name} must be an integer, got {value!r}."
        ) from exc


def env_list(name, default=None):
    value = env_str(name)
    if value is None:
        return list(default or [])
    return [item.strip() for item in value.split(",") if item.strip()]


def env_rate(name, default_per_minute):
    """Per-minute rate string for DRF, clamped so it can never fall below the floor."""
    configured = env_int(name, default_per_minute)
    return f"{max(configured, THROTTLE_FLOOR)}/min"


# ============ DERIVED CONFIG ============

def is_valid_host(entry):
    """A concrete hostname or IP. A bare wildcard is never acceptable."""
    if not entry or entry == "*":
        return False
    candidate = entry[1:] if entry.startswith(".") else entry
    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        pass
    return bool(_HOSTNAME_RE.match(candidate))


def database_config(database_url, base_dir):
    """SQLite by default; any postgres URL switches engine and parses credentials."""
    if not database_url or database_url.startswith("sqlite"):
        name = base_dir / "db.sqlite3"
        if database_url and ":///" in database_url:
            name = database_url.split(":///", 1)[1] or name
        return {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(name),
            }
        }

    if database_url.startswith(("postgres://", "postgresql://")):
        from urllib.parse import unquote, urlparse

        parsed = urlparse(database_url)
        return {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": (parsed.path or "/").lstrip("/"),
                "USER": unquote(parsed.username or ""),
                "PASSWORD": unquote(parsed.password or ""),
                "HOST": parsed.hostname or "",
                "PORT": str(parsed.port or ""),
                "CONN_MAX_AGE": 60,
            }
        }

    raise ImproperlyConfigured(
        "DATABASE_URL must be a sqlite or postgres URL."
    )


def email_config(debug):
    """Console backend is a development convenience only, never a production fallback."""
    backend = env_str("EMAIL_BACKEND")

    if backend is None:
        if debug:
            return {"EMAIL_BACKEND": CONSOLE_BACKEND}
        raise ImproperlyConfigured(
            "EMAIL_BACKEND must be set when DEBUG is false. There is no fallback: "
            "silently switching to the console backend would discard real mail."
        )

    config = {"EMAIL_BACKEND": backend}
    if backend == SMTP_BACKEND:
        missing = [
            name
            for name in ("EMAIL_HOST", "EMAIL_PORT", "DEFAULT_FROM_EMAIL")
            if env_str(name) is None
        ]
        if missing and not debug:
            # Report every absent variable at once rather than one per restart.
            raise ImproperlyConfigured(
                "The SMTP email backend is configured but these variables are "
                f"missing: {', '.join(missing)}."
            )
        config.update(
            {
                "EMAIL_HOST": env_str("EMAIL_HOST", "localhost"),
                "EMAIL_PORT": env_int("EMAIL_PORT", 587),
                "EMAIL_HOST_USER": env_str("EMAIL_HOST_USER", ""),
                "EMAIL_HOST_PASSWORD": env_str("EMAIL_HOST_PASSWORD", ""),
                "EMAIL_USE_TLS": env_bool("EMAIL_USE_TLS", True),
                "DEFAULT_FROM_EMAIL": env_str("DEFAULT_FROM_EMAIL", "no-reply@localhost"),
            }
        )
    return config


# ============ STARTUP VALIDATION ============

def validate(settings_dict):
    """Fail loudly at import time rather than serving a misconfigured process."""
    debug = settings_dict.get("DEBUG", False)
    secret_key = settings_dict.get("SECRET_KEY") or ""
    allowed_hosts = settings_dict.get("ALLOWED_HOSTS") or []

    if not secret_key:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is not set."
        )

    if debug:
        return

    if secret_key.startswith(INSECURE_KEY_PREFIX):
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is still the development key generated by "
            "django-admin startproject. Generate a fresh key before running with "
            "DEBUG false."
        )

    if not allowed_hosts:
        raise ImproperlyConfigured(
            "DJANGO_ALLOWED_HOSTS must list at least one host when DEBUG is false."
        )

    for entry in allowed_hosts:
        if not is_valid_host(entry):
            raise ImproperlyConfigured(
                f"DJANGO_ALLOWED_HOSTS entry {entry!r} is not a valid hostname or "
                "IP address. Wildcards are not permitted in production."
            )

    if not settings_dict.get("RAZORPAY_KEY_ID") or not settings_dict.get(
        "RAZORPAY_KEY_SECRET"
    ):
        raise ImproperlyConfigured(
            "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set when DEBUG is "
            "false, otherwise no payment can be taken."
        )

    if not settings_dict.get("RAZORPAY_WEBHOOK_SECRET"):
        raise ImproperlyConfigured(
            "RAZORPAY_WEBHOOK_SECRET must be set when DEBUG is false. Without it "
            "webhook signatures cannot be verified and any caller could forge a "
            "settlement."
        )

    cors_origins = settings_dict.get("CORS_ALLOWED_ORIGINS") or []
    if settings_dict.get("CORS_ALLOW_ALL_ORIGINS"):
        raise ImproperlyConfigured(
            "CORS_ALLOW_ALL_ORIGINS cannot be true when DEBUG is false."
        )
    for origin in cors_origins:
        if not origin.startswith("https://"):
            raise ImproperlyConfigured(
                f"CORS origin {origin!r} must use https when DEBUG is false."
            )
