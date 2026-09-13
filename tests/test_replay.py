"""Replaying a mission from the event log: does the reconstruction match what happened?

The one test that matters most is `test_replay_ends_where_the_index_is`: a timeline whose
last frame disagrees with the live index is a timeline that has been quietly lying about
every frame before it.
"""

from __future__ import annotations

from app.graph import layered, mission_closure
from app.replay import apply_state, edge_key, state_at, timeline, visible_edges, wire


def line_for(platform, mission_id):
    ids = mission_closure(platform.index, mission_id)
    return ids, timeline(platform.events.all(), ids, platform.index, platform.store)


def test_replay_ends_where_the_index_is(graph, platform, actor):
    platform.set_status(graph["fact"].id, "verified", actor, reason="checked")
    platform.claim(graph["calculation"].id, "someone")
    ids, line = line_for(platform, graph["mission"].id)
    final = state_at(line)
    for item_id in ids:
        assert final["status"][item_id] == platform.index.summary(item_id)["status"], item_id
    assert final["present"] == ids


def test_nothing_exists_before_its_first_frame(graph, platform):
    ids, line = line_for(platform, graph["mission"].id)
    assert state_at(line, 0)["present"] == set()
    assert state_at(line, 0)["counts"]["items"] == 0
    # the mission is created first, so one frame in exactly one item exists
    assert state_at(line, 1)["present"] == {graph["mission"].id}


def test_creation_status_is_the_one_the_item_was_created_with(graph, platform):
    """The fixture's fact is created `proposed`, not the `open` its kind defaults to."""
    _, line = line_for(platform, graph["mission"].id)
    fact = graph["fact"].id
    first = next(i for i, f in enumerate(line["frames"], 1) if f["item"] == fact and f.get("add"))
    assert state_at(line, first)["status"][fact] == "proposed"


def test_a_status_change_records_both_ends(graph, platform, actor):
    platform.set_status(graph["fact"].id, "verified", actor, reason="sources check out")
    _, line = line_for(platform, graph["mission"].id)
    moved = [f for f in line["frames"] if f["item"] == graph["fact"].id and f.get("status")]
    assert moved[-1]["from"] == "proposed"
    assert moved[-1]["status"] == "verified"
    assert "sources check out" in moved[-1]["label"]


def test_claim_and_release_move_status_without_a_status_event(graph, platform, actor):
    item = graph["calculation"].id
    platform.set_status(item, "open", actor)
    platform.claim(item, "agent-a")
    platform.release(item, "agent-a")
    _, line = line_for(platform, graph["mission"].id)
    seen = [(f["type"], f.get("status")) for f in line["frames"] if f["item"] == item and f.get("status")]
    assert ("claimed", "claimed") in seen
    assert ("released", "open") in seen


def test_an_error_run_moves_nothing(platform, graph):
    """`fail` means the answer is wrong; `error` means we learned nothing. Replay must agree."""
    ids = mission_closure(platform.index, graph["mission"].id)
    item = graph["calculation"].id
    events = platform.events.all() + [
        {
            "seq": 9001,
            "at": "2030-01-01T00:00:00Z",
            "type": "run",
            "item": item,
            "by": "t",
            "detail": {"verdict": "error", "run_id": "r1"},
        },
    ]
    line = timeline(events, ids, platform.index, platform.store)
    before = state_at(line, len(line["frames"]) - 1)["status"][item]
    after = state_at(line)
    assert after["status"][item] == before
    assert "nothing was learned" in after["frame"]["label"]


def test_invalidation_reads_as_verified_going_stale(graph, platform, actor):
    platform.set_status(graph["calculation"].id, "proposed", actor, reason="filled in")
    platform.set_status(graph["calculation"].id, "verified", actor, reason="ran clean")
    platform.update(graph["fact"].id, {"payload": {**graph["fact"].payload, "confidence": "high"}}, actor)
    _, line = line_for(platform, graph["mission"].id)
    final = state_at(line)
    assert final["status"][graph["calculation"].id] == "proposed"
    stale = [f for f in line["frames"] if f["item"] == graph["calculation"].id and f.get("status")]
    assert stale[-1]["from"] == "verified"


def test_an_edge_appears_at_the_frame_that_added_it(graph, platform, actor):
    ids, line = line_for(platform, graph["mission"].id)
    layout = layered(platform.index, ids, graph["mission"].id)
    key = edge_key(graph["fact"].id, graph["mission"].id)
    announced = set(line["announced"])
    assert key in announced
    added = next(i for i, f in enumerate(line["frames"], 1) if f.get("edge_add") == key)
    assert key not in visible_edges(state_at(line, added - 1), announced, layout["edges"])
    assert key in visible_edges(state_at(line, added), announced, layout["edges"])


def test_state_at_clamps_instead_of_exploding(graph, platform):
    _, line = line_for(platform, graph["mission"].id)
    assert state_at(line, -5)["index"] == 0
    assert state_at(line, 10_000)["index"] == len(line["frames"])


def test_apply_state_keeps_positions_and_marks_the_unborn(graph, platform):
    ids, line = line_for(platform, graph["mission"].id)
    layout = layered(platform.index, ids, graph["mission"].id)
    early = apply_state(layout, state_at(line, 1))
    shape = [[n["id"] for n in layer] for layer in layout["layers"]]
    assert [[n["id"] for n in layer] for layer in early["layers"]] == shape  # nothing moved
    absent = {n["id"] for layer in early["layers"] for n in layer if n["absent"]}
    assert graph["mission"].id not in absent
    assert graph["fact"].id in absent


def test_wire_carries_every_frame_and_no_stray_keys(graph, platform):
    _, line = line_for(platform, graph["mission"].id)
    packed = wire(line)
    assert len(packed["frames"]) == len(line["frames"])
    assert set(packed) == {"frames", "status0", "conf0", "present0", "announced"}
    assert all(
        set(f) <= {"q", "a", "t", "i", "l", "b", "n", "s", "p", "c", "e", "x"} for f in packed["frames"]
    )


def test_replay_page_renders_at_any_position(client, graph):
    mission = graph["mission"].id
    end = client.get(f"/missions/{mission}/replay")
    assert end.status_code == 200
    assert 'class="rrange"' in end.text
    start = client.get(f"/missions/{mission}/replay?at=0")
    assert start.status_code == 200
    assert start.text.count("gabsent") == len(mission_closure(client.platform.index, mission))
    assert "before anything happened" in start.text
    assert client.get(f"/missions/{mission}/replay?at=99999").status_code == 200
    assert client.get(f"/missions/{mission}/replay?at=-1").status_code == 200


def test_the_mission_page_links_to_the_replay(client, graph):
    body = client.get(f"/missions/{graph['mission'].id}").text
    assert f"/missions/{graph['mission'].id}/replay" in body
