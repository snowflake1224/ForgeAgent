from pathlib import Path

import pytest

from forgeagent.security import SecurityError, assert_safe_relative_path, resolve_under_root


@pytest.mark.parametrize("path", ["../secret", "src/../../secret", "/etc/passwd", r"..\secret"])
def test_path_traversal_and_absolute_paths_are_denied(path: str, tmp_path: Path) -> None:
    with pytest.raises(SecurityError):
        assert_safe_relative_path(path)
    with pytest.raises(SecurityError):
        resolve_under_root(tmp_path, path)
