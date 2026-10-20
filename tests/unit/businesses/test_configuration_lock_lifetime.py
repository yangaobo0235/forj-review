"""Released local locks must not retain temporary configuration directories."""

import gc
import weakref

from forj_review_service.businesses.page_config.files import _locks, configuration_lock


def test_nested_lock_reuses_live_lock_then_releases_registry_entry(tmp_path):
    root = tmp_path / "configuration"
    root.mkdir()
    key = str(root.resolve())
    with configuration_lock(root):
        first = weakref.ref(_locks[key])
        with configuration_lock(root):
            assert _locks[key] is first()
    gc.collect()
    assert first() is None
    assert key not in _locks
