"""Mission closure, the work queue, progress accounting and mermaid emission."""

from __future__ import annotations

from app.graph import (
    filter_ids,
    layered,
    mermaid,
    mission_closure,
    mission_progress,
    neighbourhood,
    open_queue,
)


def test_closure_reaches_members_and_what_they_depend_on(graph, platform):
    ids = mission_closure(platform.index, graph["mission"].id)
    assert ids == {graph[k].id for k in ("mission", "fact", "calculator", "calculation")}


def test_closure_terminates_on_a_cycle(platform, actor):
    a = platform.create({"kind": "idea", "title": "a", "payload": {"markdown": "a"}}, actor)
    b = platform.create({"kind": "idea", "title": "b", "payload": {"markdown": "b"}}, actor)
    platform.add_ref(a.id, "depends_on", b.id, "", actor)
    platform.add_ref(b.id, "depends_on", a.id, "", actor)
    assert mission_closure(platform.index, a.id) == {a.id, b.id}


def test_queue_orders_unblocked_work_first_even_with_a_cycle(platform, actor):
    mission = platform.create(
        {"kind": "idea", "title": "m", "payload": {"is_mission": True, "goal": "g"}}, actor
    )
    a = platform.create(
        {"kind": "fact", "title": "a", "question": "?", "refs": [{"rel": "part_of", "to": mission.id}]}, actor
    )
    b = platform.create(
        {"kind": "fact", "title": "b", "question": "?", "refs": [{"rel": "part_of", "to": mission.id}]}, actor
    )
    platform.add_ref(a.id, "depends_on", b.id, "", actor)
    platform.add_ref(b.id, "depends_on", a.id, "", actor)
    queue = open_queue(platform.index, mission.id)
    assert {q["id"] for q in queue} == {a.id, b.id}  # terminates, includes both


def test_queue_reasons_are_specific(graph, platform, actor):
    queue = {q["id"]: q["reason"] for q in open_queue(platform.index, graph["mission"].id)}
    assert queue[graph["fact"].id] == "filled in but not human-confirmed"
    assert graph["mission"].id not in queue  # a mission is not work on itself
    # drafts are not offered to other agents yet; opening one puts it in the queue
    assert graph["calculator"].id not in queue
    platform.set_status(graph["calculator"].id, "open", actor)
    platform.set_status(graph["calculation"].id, "proposed", actor)
    platform.set_status(graph["calculation"].id, "failed", actor)
    queue = {q["id"]: q["reason"] for q in open_queue(platform.index, graph["mission"].id)}
    assert queue[graph["calculator"].id] == "needs work"
    assert queue[graph["calculation"].id] == "last run disagreed with the claimed answer"


def test_verified_but_stale_upstream_stays_in_the_queue(graph, platform, actor):
    calc = graph["calculation"]
    platform.set_status(calc.id, "proposed", actor)  # draft -> proposed
    platform.set_status(calc.id, "verified", actor)
    entry = next(q for q in open_queue(platform.index, graph["mission"].id) if q["id"] == calc.id)
    assert entry["reason"] == "verified, but something upstream is unfinished"


def test_retired_items_leave_the_queue(graph, platform, actor):
    platform.set_status(graph["fact"].id, "abandoned", actor, reason="not needed")
    assert graph["fact"].id not in {q["id"] for q in open_queue(platform.index, graph["mission"].id)}


def test_progress_counts_tiers_separately(graph, platform, actor):
    progress = mission_progress(platform.index, graph["mission"].id)
    assert progress["items"] == 3
    assert progress["tiers"]["attested"]["total"] == 1
    assert progress["tiers"]["reproduced"]["total"] == 2
    # attested only counts as done once a human has confirmed it
    platform.confirm(graph["fact"].id, True, actor, "read it")
    after = mission_progress(platform.index, graph["mission"].id)
    assert after["tiers"]["attested"]["done"] == 1


def test_neighbourhood_expands_both_directions(graph, platform):
    near = neighbourhood(platform.index, graph["fact"].id, depth=1)
    assert graph["mission"].id in near and graph["calculation"].id in near


def test_mermaid_output_links_every_node(graph, platform):
    ids = mission_closure(platform.index, graph["mission"].id)
    source = mermaid(platform.index, ids)
    assert source.startswith("graph LR")
    assert source.count("click ") == len(ids)
    assert "|part_of|" in source
    assert "classDef st-verified" in source


def test_mermaid_collapses_a_large_graph_to_the_idea_skeleton(graph, platform):
    ids = mission_closure(platform.index, graph["mission"].id)
    source = mermaid(platform.index, ids, collapse_above=1)
    assert source.count("click ") == 1  # only the idea survives


def test_layers_put_a_mission_above_its_members_and_their_dependencies(graph, platform):
    ids = mission_closure(platform.index, graph["mission"].id)
    layout = layered(platform.index, ids, root=graph["mission"].id)
    depth = {n["id"]: n["depth"] for layer in layout["layers"] for n in layer}
    assert depth[graph["mission"].id] == 0
    # part_of is flipped for layout, so members hang under the mission
    assert depth[graph["calculation"].id] > depth[graph["mission"].id]
    # and a calculation sits above the calculator and the fact it leans on
    assert depth[graph["calculator"].id] > depth[graph["calculation"].id]
    assert depth[graph["fact"].id] > depth[graph["calculation"].id]
    assert [n["id"] for n in layout["layers"][0]] == [graph["mission"].id]


def test_layers_survive_a_cycle(platform, actor):
    a = platform.create({"kind": "idea", "title": "a", "payload": {"markdown": "a"}}, actor)
    b = platform.create({"kind": "idea", "title": "b", "payload": {"markdown": "b"}}, actor)
    platform.add_ref(a.id, "depends_on", b.id, "", actor)
    platform.add_ref(b.id, "depends_on", a.id, "", actor)
    layout = layered(platform.index, {a.id, b.id})
    assert sum(len(layer) for layer in layout["layers"]) == 2
    assert len(layout["edges"]) == 2


def test_a_pinned_root_stays_on_the_top_layer(graph, platform, actor):
    """Something in the closure depending on the mission must not push it down the page."""
    platform.add_ref(graph["fact"].id, "depends_on", graph["mission"].id, "", actor)
    ids = mission_closure(platform.index, graph["mission"].id)
    layout = layered(platform.index, ids, root=graph["mission"].id)
    assert [n["id"] for n in layout["layers"][0]] == [graph["mission"].id]


def test_every_edge_between_shown_nodes_is_emitted_once(graph, platform):
    ids = mission_closure(platform.index, graph["mission"].id)
    layout = layered(platform.index, ids)
    refs = sum(len(platform.index.refs_out(i)) for i in ids)
    assert len(layout["edges"]) == refs
    assert all(e["from"] in ids and e["to"] in ids for e in layout["edges"])


def test_filtering_drops_nodes_and_the_edges_that_touch_them(graph, platform):
    ids = mission_closure(platform.index, graph["mission"].id)
    kept = filter_ids(platform.index, ids, kinds={"idea", "calculation"})
    assert kept == {graph["mission"].id, graph["calculation"].id}
    layout = layered(platform.index, kept)
    assert all(e["from"] in kept and e["to"] in kept for e in layout["edges"])
    assert filter_ids(platform.index, ids, statuses={"proposed"}) == {graph["fact"].id}
