import os

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--require-postgres", action="store_true", default=False,
        help="Fail rather than skip PostgreSQL tests when TEST_DATABASE_URL is absent",
    )


def pytest_configure(config):
    if config.getoption("--require-postgres") and not os.getenv("TEST_DATABASE_URL"):
        raise pytest.UsageError("--require-postgres requires a disposable TEST_DATABASE_URL")
