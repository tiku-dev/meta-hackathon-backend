"""
School Management System – Django settings.

Configuration is environment-driven (.env / env vars). SQLite is the default
for the hackathon demo; set DATABASE_URL (e.g. postgres://...) in production.
"""
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=True)

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "django-insecure-hackathon-demo-key-change-me"
)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "django_filters",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "core",
    "accounts",
    "academics",
    "finance",
    "results",
    "communication",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.AuditRequestContextMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

# --- Database ---------------------------------------------------------------
if os.environ.get("DATABASE_URL"):
    import dj_database_url

    DATABASES = {
        "default": dj_database_url.config(
            conn_max_age=600, conn_health_checks=True
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# --- Auth -------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "core.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "EXCEPTION_HANDLER": "core.exceptions.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=4),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "TOKEN_OBTAIN_SERIALIZER": "accounts.serializers.RolesTokenObtainPairSerializer",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "University School Management System API",
    "DESCRIPTION": "Hackathon MVP backend built from school-management-system-blueprint.md",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # CourseOfferingViewSet is exposed under both /admin and /hod prefixes.
    "SCHEMA_MOCK_STATUS": True,
    "COMPONENT_SPLIT_PATCH": False,
}

CORS_ALLOW_ALL_ORIGINS = os.environ.get("CORS_ALLOW_ALL", "1") == "1"
CORS_ALLOWED_ORIGINS = [
    o
    for o in os.environ.get(
        "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000"
    ).split(",")
    if o
]
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "Africa/Douala")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Domain defaults (overridable per record in the DB) ---------------------
# 60_000 XAF here is only a *seed/demo* default; never a hard-coded rule.
DEFAULT_FEE_AMOUNT_XAF = int(os.environ.get("DEFAULT_FEE_AMOUNT_XAF", "60000"))
# One-time registration fee charged to NEW students before any fee payment.
# Seed/demo default only — the live amount is the admin-created FeeSchedule
# with fee_type="registration_fee"; editing that record changes the fee.
DEFAULT_REGISTRATION_FEE_XAF = int(
    os.environ.get("DEFAULT_REGISTRATION_FEE_XAF", "10000"))
DEFAULT_MAX_CREDITS = int(os.environ.get("DEFAULT_MAX_CREDITS", "30"))
ACTIVATION_TOKEN_TTL_HOURS = int(os.environ.get("ACTIVATION_TOKEN_TTL_HOURS", "72"))
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://localhost:5173")

# Payment provider: "mock" (labelled fake sandbox), "notchpay" (default),
# "stripe". Real providers run in sandbox/test mode when *_test keys are
# configured; the provider can also be chosen per payment request.
PAYMENT_PROVIDER = os.environ.get("PAYMENT_PROVIDER", "mock").lower()
# The mock provider simulates payments - enable it for the demo/sandbox only.
# Defaults to on in DEBUG, off in production; never let it credit real fees.
PAYMENT_ALLOW_MOCK = os.environ.get(
    "PAYMENT_ALLOW_MOCK", "1" if DEBUG else "0") == "1"
NOTCHPAY_PUBLIC_KEY = os.environ.get("NOTCHPAY_PUBLIC_KEY", "")
NOTCHPAY_SECRET_KEY = os.environ.get("NOTCHPAY_SECRET_KEY", "")
NOTCHPAY_HASH_KEY = os.environ.get("NOTCHPAY_HASH_KEY", "")
NOTCHPAY_BASE_URL = os.environ.get("NOTCHPAY_BASE_URL", "https://api.notchpay.co")
NOTCHPAY_WEBHOOK_URL = os.environ.get("NOTCHPAY_WEBHOOK_URL", "")
NOTCHPAY_SANDBOX = os.environ.get("NOTCHPAY_SANDBOX", "1") == "1"
# Stripe keys: the publishable key is safe to expose to a frontend; the
# secret key and webhook secret must stay server-side (env only).
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_SANDBOX = STRIPE_SECRET_KEY.startswith(("sk_test_", "rk_test_"))

# --- Email configuration ------------------------------------------------------
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend"
    if os.environ.get("EMAIL_HOST_USER") or os.environ.get("EMAIL_HOST")
    else "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "465") or 465)
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "").strip()
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "").strip().replace(" ", "")

# In Django, EMAIL_USE_SSL and EMAIL_USE_TLS are mutually exclusive.
if EMAIL_PORT == 465:
    EMAIL_USE_SSL = True
    EMAIL_USE_TLS = False
else:
    EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "0").lower() in ("1", "true", "yes")
    EMAIL_USE_TLS = (
        os.environ.get("EMAIL_USE_TLS", "1").lower() in ("1", "true", "yes")
        and not EMAIL_USE_SSL
    )

EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "10") or 10)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "").strip()
if not DEFAULT_FROM_EMAIL or (EMAIL_HOST == "smtp.gmail.com" and "@" in EMAIL_HOST_USER and ("example.edu" in DEFAULT_FROM_EMAIL or "metahackathon.edu" in DEFAULT_FROM_EMAIL)):
    DEFAULT_FROM_EMAIL = f"University Admissions <{EMAIL_HOST_USER}>" if EMAIL_HOST_USER else "no-reply@example.edu"

# --- Emergency contacts (student → administration) ---------------------------
# Published on the student "Contact administration" page. Leave blank to
# hide an entry — never invent numbers. Admins always receive urgent cases
# in-app (notification bell); email below is only an extra best-effort
# copy when SMTP is configured.
UNIVERSITY_NAME = os.environ.get("UNIVERSITY_NAME", "")
EMERGENCY_CONTACT_EMAIL = os.environ.get("EMERGENCY_CONTACT_EMAIL", "")
EMERGENCY_ALERT_EMAIL = os.environ.get("EMERGENCY_ALERT_EMAIL", "")
EMERGENCY_CONTACTS = {
    "security_name": os.environ.get(
        "EMERGENCY_CONTACT_SECURITY_NAME", "Campus Security (24/7)"),
    "security_phone": os.environ.get("EMERGENCY_CONTACT_SECURITY_PHONE", ""),
    "medical_name": os.environ.get(
        "EMERGENCY_CONTACT_MEDICAL_NAME", "Campus Medical Centre"),
    "medical_phone": os.environ.get("EMERGENCY_CONTACT_MEDICAL_PHONE", ""),
    "welfare_name": os.environ.get(
        "EMERGENCY_CONTACT_WELFARE_NAME", "Student Welfare & Counselling"),
    "welfare_phone": os.environ.get("EMERGENCY_CONTACT_WELFARE_PHONE", ""),
    "admin_name": os.environ.get(
        "EMERGENCY_CONTACT_ADMIN_NAME", "Administration / Student Affairs"),
    "admin_phone": os.environ.get("EMERGENCY_CONTACT_ADMIN_PHONE", ""),
}
