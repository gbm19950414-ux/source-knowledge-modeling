"""Synthetic coverage and identity checks for MinerU's local parsed cache."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from source_knowledge_modeling.core import KnowledgeFoundationError, digest_bytes, load_json
from source_knowledge_modeling.ingest import MineruPdfAdapter, ingest


def _page(number: int, text: str | None = None) -> dict:
    return {"page_idx": number - 1, "blocks": [{"index": 0, "type": "text", "bbox": [0, 0, 1, 1], "content": text or str(number)}]}


def _cache_file(cache: Path, name: str, numbers: list[int], *, producer: str = "mineru", tier: str = "standard", source_hash: str | None = None, privacy: str | None = None, marker: str | None = None) -> Path:
    metadata = {"producer": {"name": producer, "version": "4.0.5"}}
    if source_hash is not None:
        metadata["source_sha256"] = source_hash
    if privacy is not None:
        metadata["privacy"] = privacy
    middle = {"metadata": metadata, "extensions": {"mineru": {"tier": tier}}, "pages": [_page(n, marker) for n in numbers]}
    path = cache / name
    path.write_text(json.dumps(middle), encoding="utf-8")
    return path


def _adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, requested: str) -> tuple[MineruPdfAdapter, Path, Path]:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"synthetic pdf identity")
    source_hash = digest_bytes(source.read_bytes())
    home = tmp_path / "mineru-home"
    cache = home / "doclib" / "parsed" / source_hash[:2] / source_hash / "standard"
    cache.mkdir(parents=True)
    adapter = MineruPdfAdapter(executable=tmp_path / "mineru", home=home)

    def _response(args: list[str]) -> dict:
        if args[:2] == ["server", "status"]:
            return {"parse_server": {"local": {"healthy": True, "supported_tiers": ["standard"]}}}
        assert args[:2] == ["parse", str(source)]
        assert args[args.index("--pages") + 1] == requested
        return {"parse": {"status": "done", "sha256": source_hash, "tier": "standard", "page_range": requested, "privacy": "local"}}

    monkeypatch.setattr(adapter, "_run_json", _response)
    return adapter, source, cache


def test_local_cache_combines_noncontiguous_files_and_orders_physical_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, source, cache = _adapter(tmp_path, monkeypatch, requested="2-5")
    first = _cache_file(cache, "1-2,5-6_100.json", [1, 2, 5, 6])
    second = _cache_file(cache, "3-4_200.json", [3, 4])
    units, parser = adapter.extract(source, pages="2-5")
    assert [unit.locator for unit in units] == [f"pdf-page:{n}/block:0" for n in range(2, 6)]
    assert [unit.text for unit in units] == [str(n) for n in range(2, 6)]
    assert parser["page_range"] == "2-5" and parser["privacy"] == "local"
    assert parser["input_files"] == [
        {"cache_file": first.name, "sha256": digest_bytes(first.read_bytes())},
        {"cache_file": second.name, "sha256": digest_bytes(second.read_bytes())},
    ]
    assert parser["input_sha256"] not in {item["sha256"] for item in parser["input_files"]}
    _, snapshot_path = ingest(source, root=tmp_path, workspace=tmp_path / "runs", adapter=adapter, pages="2-5")
    assert load_json(snapshot_path)["parser"]["input_files"] == parser["input_files"]


def test_local_cache_rejects_missing_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, source, cache = _adapter(tmp_path, monkeypatch, requested="2-5")
    _cache_file(cache, "1-2_100.json", [1, 2])
    _cache_file(cache, "4-5_200.json", [4, 5])
    with pytest.raises(KnowledgeFoundationError, match=r"does not cover.*\[3\]"):
        adapter.extract(source, pages="2-5")


def test_local_cache_uses_latest_revision_per_duplicate_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, source, cache = _adapter(tmp_path, monkeypatch, requested="2-4")
    _cache_file(cache, "2-4_100.json", [2, 3, 4], marker="old")
    _cache_file(cache, "3_200.json", [3], marker="new")
    units, parser = adapter.extract(source, pages="2-4")
    assert [unit.text for unit in units] == ["old", "new", "old"]
    assert {item["cache_file"] for item in parser["input_files"]} == {"2-4_100.json", "3_200.json"}


@pytest.mark.parametrize("fault,expected", [
    ({"producer": "other"}, "producer identity"),
    ({"tier": "advanced"}, "tier identity"),
    ({"privacy": "remote"}, "privacy identity"),
    ({"source_hash": "0" * 64}, "source identity"),
    ({"numbers": [2, 4]}, "page coverage differ"),
])
def test_local_cache_rejects_latest_invalid_identity_or_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: dict, expected: str) -> None:
    adapter, source, cache = _adapter(tmp_path, monkeypatch, requested="2-3")
    _cache_file(cache, "2-3_100.json", [2, 3])
    values = {"producer": "mineru", "tier": "standard", "numbers": [2, 3]}
    values.update(fault)
    _cache_file(cache, "2-3_200.json", values.pop("numbers"), **values)
    with pytest.raises(KnowledgeFoundationError, match=expected):
        adapter.extract(source, pages="2-3")
