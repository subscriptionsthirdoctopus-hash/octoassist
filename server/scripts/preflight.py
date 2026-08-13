#!/usr/bin/env python3
"""Fast pre-flight checks for the OctoAssist server.

Catches the two classes of breakage that reach production as a 500 rather
than as an obvious failure, and that no test currently covers:

  1. A Jinja template that does not compile. The app is template-heavy and
     templates are only parsed when a request first renders them, so a stray
     ``{% endif %}`` ships happily and blows up on the page that uses it.

  2. A route that does not resolve. FastAPI includes routers lazily, so simply
     importing the app proves very little. Generating the OpenAPI schema forces
     every path operation to be built, which exercises signatures, dependencies
     and response models.

Neither check needs a database: settings all have defaults and SQLAlchemy's
create_engine is lazy, so nothing connects.

Run locally from anywhere:

    python server/scripts/preflight.py
"""
from __future__ import annotations

import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = SERVER_DIR / "app" / "templates"

# Import `app.*` the same way the server does, regardless of cwd.
sys.path.insert(0, str(SERVER_DIR))

# No OCTOASSIST_UPLOAD_DIR override is set here on purpose. Importing the app
# must not touch the filesystem, so this check runs against the real defaults:
# if anyone reintroduces an import-time mkdir against a production path, the
# import fails here rather than being papered over by a temp directory.


def check_templates() -> list[str]:
    """Compile every template. Compilation catches syntax errors and unknown
    tags. Undefined filters and globals are a render-time concern and are not
    claimed here."""
    from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    # Register the app's real filters/globals so a template referencing one is
    # not what makes this fail.
    try:
        from fastapi.templating import Jinja2Templates

        from app.jinja_filters import install_on

        env = install_on(Jinja2Templates(directory=str(TEMPLATE_DIR))).env
    except Exception as e:  # pragma: no cover - falls back to a bare env
        print(f"  note: using bare Jinja env ({type(e).__name__}: {e})")

    names = sorted(p.name for p in TEMPLATE_DIR.glob("*.html"))
    failures = []
    for name in names:
        try:
            env.get_template(name)
        except TemplateSyntaxError as e:
            failures.append(f"{name}:{e.lineno}: {e.message}")
        except Exception as e:
            failures.append(f"{name}: {type(e).__name__}: {e}")
    print(f"  {len(names) - len(failures)}/{len(names)} templates compile")
    return failures


def check_routes() -> list[str]:
    """Import the app and force every lazily-included route to be built."""
    try:
        from app.main import app
    except Exception as e:
        return [f"importing app.main failed: {type(e).__name__}: {e}"]
    try:
        schema = app.openapi()
    except Exception as e:
        return [f"building the OpenAPI schema failed: {type(e).__name__}: {e}"]
    paths = schema.get("paths", {})
    operations = sum(len(v) for v in paths.values())
    if not paths:
        return ["OpenAPI schema resolved to zero paths — routers not included?"]
    print(f"  {len(paths)} paths / {operations} operations resolve")
    return []


def main() -> int:
    failed = False
    for label, check in (("templates", check_templates), ("routes", check_routes)):
        print(f"checking {label}…")
        problems = check()
        for p in problems:
            print(f"  FAIL {p}")
        failed = failed or bool(problems)
    print("preflight: FAILED" if failed else "preflight: OK")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
