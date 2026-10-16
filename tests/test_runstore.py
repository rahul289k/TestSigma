import json

from triangulate.runstore import _without_geometry


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def test_geometry_does_not_change_the_content_hash(tmp_path):
    a = _write(tmp_path / "a.jsonl", [{"fp": "S-1", "elements": [{"name": "Send", "bbox": [0, 49, 48, 48]}]}])
    b = _write(tmp_path / "b.jsonl", [{"fp": "S-1", "elements": [{"name": "Send", "bbox": [0, 81, 48, 48]}]}])
    c = _write(tmp_path / "c.jsonl", [{"fp": "S-1", "elements": [{"name": "Cancel", "bbox": [0, 49, 48, 48]}]}])
    assert _without_geometry(a) == _without_geometry(b)       # a banner moved the control: same content
    assert _without_geometry(a) != _without_geometry(c)       # a different control: different content


def test_files_without_geometry_are_hashed_byte_for_byte(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"b": 1, "a": 2}')
    assert _without_geometry(p) == p.read_bytes()
