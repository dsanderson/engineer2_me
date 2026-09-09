"""Atomic writes, revisions, attachments, runs, and the index."""

from __future__ import annotations

import json

import pytest

from app.index import Index
from app.models import Actor, Item, ValidationError
from app.store import NotFound, atomic_write


def test_atomic_write_leaves_no_torn_file(tmp_path):
    path = tmp_path / "a" / "item.json"
    atomic_write(path, '{"ok": true}')
    assert json.loads(path.read_text()) == {"ok": True}
    assert not list(tmp_path.glob("**/*.tmp"))


def test_every_write_archives_the_prior_revision(platform, actor):
    item = platform.create({"kind": "idea", "title": "one", "payload": {"markdown": "a"}}, actor)
    platform.update(item.id, {"title": "two"}, actor)
    platform.update(item.id, {"title": "three"}, actor)
    revs = platform.store.list_revisions(item.id)
    assert [r["rev"] for r in revs] == [1, 2, 3]
    assert platform.store.get_revision(item.id, 1).title == "one"
    assert platform.store.get(item.id).title == "three"


def test_nothing_can_be_deleted(platform, actor):
    item = platform.create({"kind": "idea", "title": "dead end", "payload": {"markdown": "x"}}, actor)
    platform.set_status(item.id, "abandoned", actor, reason="not worth it")
    assert platform.store.get(item.id).status == "abandoned"
    assert platform.store.exists(item.id)


def test_deprecation_requires_a_reason(platform, actor):
    item = platform.create({"kind": "idea", "title": "x", "payload": {"markdown": "x"}}, actor)
    with pytest.raises(ValidationError):
        platform.set_status(item.id, "deprecated", actor, reason="")


def test_attachments_are_hashed_and_replace_by_name(platform, actor):
    item = platform.create({"kind": "idea", "title": "x", "payload": {"markdown": "x"}}, actor)
    meta = platform.attach_bytes(item.id, "note.txt", b"hello", "text/plain", actor)
    assert meta["bytes"] == 5 and len(meta["sha256"]) == 64
    platform.attach_bytes(item.id, "note.txt", b"hello again", "text/plain", actor)
    reloaded = platform.store.get(item.id)
    assert len(reloaded.attachments) == 1
    assert platform.store.read_file(item.id, "note.txt") == b"hello again"


def test_unsafe_filenames_are_refused(platform, actor):
    item = platform.create({"kind": "idea", "title": "x", "payload": {"markdown": "x"}}, actor)
    with pytest.raises(ValidationError):
        platform.attach_bytes(item.id, "../../etc/passwd", b"x", "text/plain", actor)


def test_runs_are_addressable_without_the_item(platform, actor):
    item = platform.create({"kind": "idea", "title": "x", "payload": {"markdown": "x"}}, actor)
    platform.store.write_run(item.id, {"run_id": "run_abc", "verdict": "pass"})
    assert platform.store.find_run("run_abc")["verdict"] == "pass"
    with pytest.raises(NotFound):
        platform.store.find_run("run_nope")


def test_index_rebuild_matches_incremental(graph, platform):
    incremental = {i: e.summary for i, e in platform.index.entries.items()}
    fresh = Index(platform.store)
    fresh.rebuild()
    assert {i: e.summary for i, e in fresh.entries.items()} == incremental
    assert fresh.refs_in(graph["mission"].id)


def test_index_cache_round_trips_and_deleting_it_is_safe(graph, platform):
    platform.index.save()
    reloaded = Index(platform.store)
    reloaded.load()
    assert len(reloaded) == len(platform.index)
    reloaded.cache_path.unlink()
    again = Index(platform.store)
    again.load()
    assert len(again) == len(platform.index)


def test_index_cache_is_ignored_when_stale(graph, platform, actor):
    platform.index.save()
    platform.update(graph["fact"].id, {"title": "moved on"}, actor)
    fresh = Index(platform.store)
    fresh.load()
    assert fresh.summary(graph["fact"].id)["title"] == "moved on"


def test_backlinks_appear_and_disappear(graph, platform, actor):
    mission, fact = graph["mission"], graph["fact"]
    assert any(b.frm == fact.id for b in platform.index.refs_in(mission.id))
    platform.remove_ref(fact.id, "part_of", mission.id, "wrong mission", actor)
    assert not any(b.frm == fact.id for b in platform.index.refs_in(mission.id))


def test_removing_a_ref_requires_a_reason(graph, platform, actor):
    with pytest.raises(ValidationError):
        platform.remove_ref(graph["fact"].id, "part_of", graph["mission"].id, "", actor)


def test_store_rejects_a_duplicate_id(platform):
    item = Item(kind="idea", title="x", payload={"markdown": "x"})
    platform.store.create(item, Actor())
    with pytest.raises(ValidationError):
        platform.store.create(item, Actor())
