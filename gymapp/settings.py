"""Django settings for the gymapp project.

Every environment-specific value is read through gymapp.config. No secret is
written literally in this file, and config.validate() runs as the last statement
so a misconfigured process fails at import instead of serving traffic.
"""
import datetime
import os
import sys
from pathlib import Path

from gymapp import config

BASE_DIR = Path(__file__).resolve().parent.parent

config.load_dotenv(BASE_DIR / ".env")


# ============ CORE ============

SECRET_KEY = config.env_str("DJANGO_SECRET_KEY")

# Absent means production. The safe default is the strict one.
DEBUG = config.env_bool("DJANGO_DEBUG", False)

ALLOWED_HOSTS = config.env_list(
    "DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1"] if DEBUG else []
)


# ============ APPLICATIONS ============

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    # Required for logout and for refresh-token rotation to actually retire tokens.
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "core",
]

AUTH_USER_MODEL = "core.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # CORS must precede CommonMiddleware so preflight responses carry the headers.
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "gymapp.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "gymapp.wsgi.application"
ASGI_APPLICATION = "gymapp.asgi.application"


# ============ DATABASE ============

DATABASES = config.database_config(config.env_str("DATABASE_URL"), BASE_DIR)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ============ AUTHENTICATION ============

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---- Test-only password hashing ------------------------------------------------
# The suite creates hundreds of users across the property tests and PBKDF2 is
# deliberately slow by design, which accounts for most of the wall-clock time.
# MD5 is substituted under the test runner only.
#
# Detection is `"pytest" in sys.modules` rather than `"test" in sys.argv`: an argv
# scan matches any command line that merely contains the word "test", including a
# directory name, which is exactly the kind of accident that must not weaken
# password storage.
#
# The `DEBUG` conjunct is the fail-safe. If the detection ever misfires on a
# production process, DEBUG is false there and the strong hashers stay in place.
# Nothing is raised, because a hard failure here would take down a running
# service over what is only a performance concern.
RUNNING_UNDER_TEST = (
    "pytest" in sys.modules
    or "PYTEST_CURRENT_TEST" in os.environ
    or os.environ.get("DJANGO_TEST_FAST_HASHING") == "1"
)

if RUNNING_UNDER_TEST and DEBUG:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


# ============ DJANGO REST FRAMEWORK ============

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "core.authentication.DistinguishingJWTAuthentication",
    ],
    # Closed by default: RoleAllowed refuses any view that does not declare
    # `allowed_roles`, so a new view with no declaration is denied, not open.
    "DEFAULT_PERMISSION_CLASSES": [
        "core.permissions.RoleAllowed",
    ],
    # One error envelope for every non-2xx response, including errors raised
    # inside services rather than serializers.
    "EXCEPTION_HANDLER": "core.exceptions.api_exception_handler",
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "core.throttling.FloorClampedScopedThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        # Clamped to a floor of 5/min so a bad value cannot lock everyone out.
        "login": config.env_rate("THROTTLE_LOGIN_PER_MINUTE", 10),
        "registration": config.env_rate("THROTTLE_REGISTRATION_PER_MINUTE", 5),
        "password_reset": config.env_rate("THROTTLE_PASSWORD_RESET_PER_MINUTE", 5),
    },
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
}


# ============ SIMPLE JWT ============

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": datetime.timedelta(
        minutes=config.env_int("ACCESS_TOKEN_LIFETIME_MINUTES", 15)
    ),
    "REFRESH_TOKEN_LIFETIME": datetime.timedelta(
        days=config.env_int("REFRESH_TOKEN_LIFETIME_DAYS", 7)
    ),
    # Rotation plus blacklisting makes a refresh token single-use, so a stolen one
    # is useful only until the legitimate client next refreshes.
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_TYPE_CLAIM": "token_type",
    "JTI_CLAIM": "jti",
}


# ============ CORS ============

# No wildcard. Browser credentials plus an open origin list is how a token gets
# read by any site the user happens to visit.
CORS_ALLOWED_ORIGINS = config.env_list(
    "CORS_ALLOWED_ORIGINS",
    ["http://localhost:3000", "http://127.0.0.1:3000"] if DEBUG else [],
)
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = False  # Auth travels in the Authorization header, not cookies.
CORS_ALLOW_METHODS = ["DELETE", "GET", "OPTIONS", "PATCH", "POST", "PUT"]
CORS_ALLOW_HEADERS = [
    "accept",
    "authorization",
    "content-type",
    "origin",
    "user-agent",
    "x-requested-with",
]
CORS_URLS_REGEX = r"^/api/.*$"

CSRF_TRUSTED_ORIGINS = [
    origin for origin in CORS_ALLOWED_ORIGINS if origin.startswith("https://")
]


# ============ PAYMENT GATEWAY ============

# KEY_ID is public and shipped to the browser. KEY_SECRET and WEBHOOK_SECRET are
# never serialised into a response and never logged.
RAZORPAY_KEY_ID = config.env_str("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = config.env_str("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = config.env_str("RAZORPAY_WEBHOOK_SECRET", "")
RAZORPAY_ACCOUNT_CURRENCY = config.env_str("RAZORPAY_ACCOUNT_CURRENCY", "INR")

SAAS_TRIAL_DAYS = config.env_int("SAAS_TRIAL_DAYS", 14)
SAAS_INVOICE_LEAD_DAYS = config.env_int("SAAS_INVOICE_LEAD_DAYS", 7)


# ============ AI REVENUE RECOVERY ============

# None of these widen what the recovery agent is permitted to do. The agent treats the
# language model as an untrusted planner and re-checks every money and tenancy decision
# in Python afterwards, so these settings only decide *which planner is asked*.
#
# Absent RECOVERY_LLM_CLIENT means the offline deterministic planner. That is why
# `run_recovery_batch --synthetic-data` runs with no API key and opens no socket, and it
# is also the fallback when a hosted model errors: a dunning batch that stops because an
# inference endpoint is down is worse than one that sends the obvious reminder.
#
# To use a hosted model instead:
#   pip install openai
#   RECOVERY_LLM_CLIENT=core.services.recovery_agent.OpenAIToolCallingClient
#   OPENAI_API_KEY=<key>
#
# The planner speaks the OpenAI chat-completions tool-calling API but is not tied to
# OpenAI: RECOVERY_LLM_BASE_URL points the same SDK at any provider implementing that
# surface, so which model answers is a settings change rather than a code change.
RECOVERY_LLM_CLIENT = config.env_str("RECOVERY_LLM_CLIENT")
RECOVERY_LLM_MODEL = config.env_str("RECOVERY_LLM_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = config.env_str("OPENAI_API_KEY", "")
RECOVERY_LLM_BASE_URL = config.env_str("RECOVERY_LLM_BASE_URL", "")
# `required` forces the planner to call a tool. Some OpenAI-compatible providers
# accept only `auto`; a planner that returns no tool call is treated as unusable and
# the deterministic planner takes the invoice, so the guardrails do not depend on this.
RECOVERY_LLM_TOOL_CHOICE = config.env_str("RECOVERY_LLM_TOOL_CHOICE", "required")

# Where a recovery payment link points. The Razorpay adapter here exposes *orders*
# rather than hosted Payment Links, so the link addresses the platform's own checkout
# page, which opens Razorpay Checkout with the order reference and the public key. No
# secret is ever part of a link.
RECOVERY_CHECKOUT_BASE_URL = config.env_str(
    "RECOVERY_CHECKOUT_BASE_URL", "https://pay.gymapp.example/checkout"
)


# ============ EMAIL ============

vars().update(config.email_config(DEBUG))


# ============ INTERNATIONALIZATION ============

LANGUAGE_CODE = "en-us"
# Stored instants are UTC. Membership dates are evaluated in each gym's own
# timezone, which lives on the Gym row rather than here.
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# ============ STATIC AND MEDIA ============

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"


# ============ SECURITY HARDENING ============

# Applied regardless of DEBUG: no development workflow needs these relaxed.
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000  # one year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    # Trust the proxy's scheme header; without this SSL redirect loops behind a
    # load balancer that terminates TLS.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# ============ LOGGING ============

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Payment logs carry references and amounts only, never request bodies.
        "core.payments": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "core.auth": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}


# ============ STARTUP VALIDATION ============

# Last statement on purpose: a violated check prevents the process from starting.
config.validate(vars())
