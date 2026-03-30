"""
Jinja2 Environment — Django/Jinja2 bridge for Evolet templates.
================================================================
Configures the Jinja2 Environment used by all pipeline templates.

Globals injected into every template context
--------------------------------------------
  static(path)              → Django's staticfiles URL (same as {% static %})
  url(name, *args, **kwargs)→ Django's reverse() for named URL patterns
  csrf_token_func(request)  → CSRF token string (for JS / fetch usage)
  csrf_input_func(request)  → Hidden <input> HTML tag with CSRF token

Extensions enabled
------------------
  jinja2.ext.loopcontrols   → adds {% break %} and {% continue %} inside loops

The environment() function is referenced in TEMPLATES[0]["OPTIONS"]["environment"]
in settings.py.
"""

from django.middleware.csrf import get_token
from django.templatetags.static import static
from django.urls import reverse
from jinja2 import Environment


def _url(name: str, *args, **kwargs) -> str:
    """
    Resolve a named URL pattern to its URL string.

    Wraps Django's reverse() and transparently handles both positional
    args (e.g. url("pipeline:patient_detail", patient.id)) and keyword
    args (e.g. url("pipeline:patient_detail", patient_id=patient.id)).
    """
    if args:
        return reverse(name, args=args)
    elif kwargs:
        return reverse(name, kwargs=kwargs)
    else:
        return reverse(name)


def _csrf_token(request) -> str:
    """Return the CSRF token string for the current request session."""
    return get_token(request)


def _csrf_input(request) -> str:
    """
    Return a complete hidden <input> HTML element carrying the CSRF token.

    Used in Jinja2 templates where {% csrf_input %} (a Django tag) is not
    available.  Typically called as:
        {{ csrf_input_func(request)|safe }}
    """
    token = get_token(request)
    return f'<input type="hidden" name="csrfmiddlewaretoken" value="{token}">'


def environment(**options) -> Environment:
    """
    Build and return the Jinja2 Environment for this Django project.

    Called once at startup by django-jinja.  The 'extensions' key is
    popped from options before passing to Environment() because
    django-jinja may inject its own extension list — we override it
    with only the extensions we need.
    """
    # django-jinja may pass its own extensions list; override cleanly
    options.pop("extensions", None)

    env = Environment(
        **options,
        extensions=["jinja2.ext.loopcontrols"],
    )

    # Inject Django helpers as template globals so every template can
    # call static(), url(), and the CSRF helpers without imports.
    env.globals.update({
        "static":          static,
        "url":             _url,
        "csrf_token_func": _csrf_token,
        "csrf_input_func": _csrf_input,
    })

    return env
