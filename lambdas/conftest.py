"""All tests are offline, with fictitious credentials (including future tests)."""
import socket

import pytest


@pytest.fixture(autouse=True)
def offline_aws(monkeypatch):
    monkeypatch.delenv("GOVERNANCE_SCOPE", raising=False)
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "eu-west-1",
        "AWS_EC2_METADATA_DISABLED": "true",
    }.items():
        monkeypatch.setenv(key, value)

    def deny_network(*args, **kwargs):
        raise AssertionError("Network access is forbidden in the local test suite")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
