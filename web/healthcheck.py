"""Docker HEALTHCHECK for the web interface: exit 0 when /status answers, 1 otherwise.

Standard library only. Uses RUNNER_PORT and RUNNER_TOKEN, like runner.py.
"""
import os
import sys
import urllib.request


def healthy(port=None, token=None, timeout=5):
    port = port or os.environ.get("RUNNER_PORT", "8080")
    token = os.environ.get("RUNNER_TOKEN", "") if token is None else token
    request = urllib.request.Request(f"http://127.0.0.1:{port}/status")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout):  # raises on 401, 404, 5xx
            return True
    except OSError:  # also urllib's HTTPError and timeouts
        return False


if __name__ == "__main__":
    sys.exit(0 if healthy() else 1)
