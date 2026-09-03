"""
Vercel entry point (@vercel/python).

Vercel's Python runtime looks for a WSGI-compatible callable named
``app`` in this file and routes all requests to it (see vercel.json's
"src": "api/index.py"). This just wraps Django's existing WSGI app —
no Django code is duplicated here.
"""

import os
import sys

# Make the project root (one level up from api/) importable so
# "gymapp.settings" and friends resolve correctly in Vercel's runtime.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gymapp.settings")

from django.core.wsgi import get_wsgi_application  # noqa: E402

app = get_wsgi_application()
