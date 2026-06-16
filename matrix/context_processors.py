"""Expose the Matrix feature flag to templates so the nav link only shows when
the integration is enabled. Registered in settings.TEMPLATES.
"""
from __future__ import annotations

from django.conf import settings


def matrix_flags(request):
    return {"matrix_enabled": settings.MATRIX_ENABLED}
