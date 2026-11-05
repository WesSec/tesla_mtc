"""version.py and CHANGELOG.md must agree, so every release is documented."""
import re
from pathlib import Path

from version import __version__

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
HEADING = re.compile(r"^## \[([^\]]+)\]", re.MULTILINE)


def test_version_is_semver():
    assert SEMVER.match(__version__), f"version.py has {__version__!r}, expected x.y.z"


def test_changelog_documents_the_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = [v for v in HEADING.findall(changelog) if v.lower() != "unreleased"]

    assert versions, "CHANGELOG.md has no version headings"
    assert versions[0] == __version__, (
        f"version.py is {__version__} but the newest version in CHANGELOG.md is {versions[0]}; "
        "bump both together")
