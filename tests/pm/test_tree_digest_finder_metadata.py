from pm.store import tree_digest


def test_finder_metadata_does_not_change_package_digest(tmp_path):
    root = tmp_path / "package"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "python").write_bytes(b"original")
    before = tree_digest(root)
    (root / ".DS_Store").write_bytes(b"finder metadata")
    (root / "bin" / ".DS_Store").write_bytes(b"more metadata")
    assert tree_digest(root) == before
    (root / "bin" / "python").write_bytes(b"changed")
    assert tree_digest(root) != before
