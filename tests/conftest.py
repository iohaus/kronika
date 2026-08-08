from __future__ import annotations

import os

# Must be set before `application.config` is first imported anywhere in the test
# session — its Settings() singleton reads this once at import time. Without this,
# tests that exercise application.main / kronika-console.bridge (both of which
# construct a live HttpDataHubWriter by default) would write real incidents,
# annotations, and tags into whatever DataHub instance DATAHUB_SERVER_URL points at
# on every test run.
os.environ.setdefault("KRONIKA_WRITER_MOCK_MODE", "true")
