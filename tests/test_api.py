"""The HTTP surface an agent actually uses."""

from __future__ import annotations


def create(client, **kw):
    resp = client.post("/api/v1/items", json=kw)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_health_and_stats(client):
    assert client.get("/api/v1/health").json()["ok"] is True
    assert client.get("/api/v1/stats").json()["items"] == 0


def test_create_read_patch_and_revisions(client):
    item = create(client, kind="fact", title="E of 6061-T6", question="What is E?", by="agent-alpha")
    assert item["status"] == "open" and item["tier"] == "attested"
    assert item["created_by"] == {"type": "agent", "name": "agent-alpha"}

    got = client.get(f"/api/v1/items/{item['id']}").json()
    assert got["title"] == "E of 6061-T6"
    assert got["url"].endswith(f"/items/{item['id']}")

    patched = client.patch(
        f"/api/v1/items/{item['id']}",
        json={
            "payload": {"data": {"E_GPa": 68.9}, "sources": [{"url": "https://matweb"}]},
            "status": "proposed",
            "by": "agent-alpha",
        },
    ).json()
    assert patched["rev"] == 2 and patched["status"] == "proposed"

    revs = client.get(f"/api/v1/items/{item['id']}/revisions").json()["revisions"]
    assert [r["rev"] for r in revs] == [1, 2]
    assert client.get(f"/api/v1/items/{item['id']}/revisions/1").json()["payload"] == {}


def test_validation_errors_are_400_with_detail(client):
    resp = client.post(
        "/api/v1/items", json={"kind": "fact", "title": "no source", "payload": {"data": {"a": 1}}}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid"
    assert any("source" in e for e in resp.json()["error"]["detail"]["errors"])


def test_missing_item_is_404(client):
    assert client.get("/api/v1/items/8a1c0000-0000-4000-8000-000000000000").status_code == 404


def test_illegal_transition_is_409(client):
    item = create(client, kind="idea", title="dead end", payload={"markdown": "x"})
    client.post(f"/api/v1/items/{item['id']}/status", json={"status": "abandoned", "reason": "no"})
    resp = client.post(f"/api/v1/items/{item['id']}/status", json={"status": "open"})
    assert resp.status_code == 409


def test_deprecating_links_the_replacement(client):
    old = create(client, kind="fact", title="old value", question="?")
    new = create(client, kind="fact", title="new value", question="?")
    resp = client.post(
        f"/api/v1/items/{old['id']}/status",
        json={"status": "deprecated", "reason": "superseded by MMPDS", "superseded_by": new["id"]},
    )
    assert resp.status_code == 200
    refs = client.get(f"/api/v1/items/{new['id']}/refs").json()["out"]
    assert any(r["rel"] == "supersedes" and r["to"] == old["id"] for r in refs)


def test_claims_are_advisory_but_contested_claims_are_409(client):
    item = create(client, kind="fact", title="contested", question="?")
    assert client.post(f"/api/v1/items/{item['id']}/claim", json={"agent": "alpha"}).status_code == 200
    assert client.get(f"/api/v1/items/{item['id']}").json()["status"] == "claimed"
    contested = client.post(f"/api/v1/items/{item['id']}/claim", json={"agent": "beta"})
    assert contested.status_code == 409 and "alpha" in contested.json()["error"]["message"]
    # the holder may re-claim (extend), and release returns it to the queue
    assert client.post(f"/api/v1/items/{item['id']}/claim", json={"agent": "alpha"}).status_code == 200
    assert client.post(f"/api/v1/items/{item['id']}/release", json={"agent": "alpha"}).status_code == 200
    assert client.get(f"/api/v1/items/{item['id']}").json()["status"] == "open"


def test_refs_add_remove_and_backlinks(client):
    mission = create(client, kind="idea", title="mission", payload={"is_mission": True, "goal": "g"})
    fact = create(client, kind="fact", title="member", question="?")
    assert (
        client.post(
            f"/api/v1/items/{fact['id']}/refs", json={"rel": "part_of", "to": mission["id"]}
        ).status_code
        == 201
    )
    assert client.get(f"/api/v1/items/{mission['id']}/refs").json()["in"][0]["from"] == fact["id"]

    bad = client.post(f"/api/v1/items/{fact['id']}/refs", json={"rel": "related", "to": mission["id"]})
    assert bad.status_code == 400  # 'related' requires a note

    silent = client.post(
        f"/api/v1/items/{fact['id']}/refs/remove", json={"rel": "part_of", "to": mission["id"]}
    )
    assert silent.status_code == 400  # removals are logged, not silent
    ok = client.post(
        f"/api/v1/items/{fact['id']}/refs/remove",
        json={"rel": "part_of", "to": mission["id"], "reason": "wrong mission"},
    )
    assert ok.status_code == 200
    assert client.get(f"/api/v1/items/{mission['id']}/refs").json()["in"] == []


def test_implied_edges_are_materialised(client):
    calculator = create(
        client,
        kind="calculator",
        title="calc",
        payload={"script": "def run(i):\n    return {}", "examples": []},
    )
    calculation = create(
        client,
        kind="calculation",
        title="calculation",
        payload={"calculator": calculator["id"], "inputs": {}, "expect": {"x": 1}},
    )
    rels = [r["rel"] for r in calculation["refs"]]
    assert "uses_calculator" in rels


def test_script_payload_becomes_a_hashed_file(client):
    item = create(
        client,
        kind="calculator",
        title="calc",
        payload={"script": "def run(i):\n    return {'x': 1}\n", "examples": []},
    )
    assert item["payload"]["entrypoint"] == "calc.py"
    assert "script" not in item["payload"]
    assert len(item["payload"]["sha256"]) == 64
    body = client.get(f"/api/v1/items/{item['id']}/files/calc.py")
    assert body.status_code == 200 and b"def run" in body.content


def test_file_upload_and_download(client):
    item = create(client, kind="fact", title="with a datasheet", question="?")
    resp = client.post(
        f"/api/v1/items/{item['id']}/files",
        files={"file": ("sheet.txt", b"E = 68.9 GPa", "text/plain")},
        data={"by": "agent-alpha", "source_url": "https://matweb"},
    )
    assert resp.status_code == 201 and resp.json()["bytes"] == 12
    assert client.get(f"/api/v1/items/{item['id']}/files/sheet.txt").content == b"E = 68.9 GPa"
    assert (
        client.get(f"/api/v1/items/{item['id']}").json()["attachments"][0]["source_url"] == "https://matweb"
    )


def test_list_filters(client):
    mission = create(client, kind="idea", title="mission", payload={"is_mission": True, "goal": "g"})
    create(
        client,
        kind="fact",
        title="tagged",
        question="?",
        tags=["materials"],
        refs=[{"rel": "part_of", "to": mission["id"]}],
    )
    create(client, kind="fact", title="untagged elsewhere", question="?")

    assert len(client.get("/api/v1/items?kind=fact").json()["items"]) == 2
    assert len(client.get("/api/v1/items?tag=materials").json()["items"]) == 1
    assert len(client.get("/api/v1/items?q=untagged").json()["items"]) == 1
    assert len(client.get(f"/api/v1/items?mission={mission['id']}&kind=fact").json()["items"]) == 1
    assert client.get("/api/v1/items?limit=1").json()["next_cursor"] == "1"


def test_events_feed_tails_by_sequence(client):
    create(client, kind="fact", title="one", question="?", by="agent-alpha")
    events = client.get("/api/v1/events").json()["events"]
    assert events[0]["type"] == "created" and events[0]["by"] == "agent-alpha"
    assert client.get(f"/api/v1/events?since={events[-1]['seq']}").json()["events"] == []


def test_mission_discovery_endpoints(client):
    mission = create(
        client,
        kind="idea",
        title="mission",
        payload={"is_mission": True, "goal": "g", "milestones": [{"label": "materials", "item": None}]},
    )
    fact = create(
        client, kind="fact", title="open work", question="?", refs=[{"rel": "part_of", "to": mission["id"]}]
    )

    missions = client.get("/api/v1/missions").json()["missions"]
    assert missions[0]["id"] == mission["id"]
    assert missions[0]["progress"]["items"] == 1

    queue = client.get(f"/api/v1/missions/{mission['id']}/open").json()["open"]
    assert [q["id"] for q in queue] == [fact["id"]]
    assert queue[0]["reason"] == "needs work"

    graph = client.get(f"/api/v1/missions/{mission['id']}/graph").json()
    assert {n["id"] for n in graph["nodes"]} == {mission["id"], fact["id"]}
    assert "graph LR" in graph["mermaid"]

    milestones = client.get(f"/api/v1/missions/{mission['id']}/milestones").json()["milestones"]
    assert milestones[0]["status"] == "unassigned"


def test_open_queue_puts_unblocked_work_first(client):
    mission = create(client, kind="idea", title="m", payload={"is_mission": True, "goal": "g"})
    upstream = create(
        client, kind="fact", title="upstream", question="?", refs=[{"rel": "part_of", "to": mission["id"]}]
    )
    downstream = create(
        client,
        kind="fact",
        title="downstream",
        question="?",
        refs=[{"rel": "part_of", "to": mission["id"]}, {"rel": "depends_on", "to": upstream["id"]}],
    )
    queue = client.get(f"/api/v1/missions/{mission['id']}/open").json()["open"]
    assert [q["id"] for q in queue] == [upstream["id"], downstream["id"]]
    assert queue[1]["blocked_by"][0]["id"] == upstream["id"]


def test_export_returns_the_whole_graph(client):
    create(client, kind="fact", title="one", question="?")
    assert len(client.get("/api/v1/export").json()["items"]) == 1


def test_onboarding_docs_are_served_as_markdown(client):
    resp = client.get("/start.md")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "engineer2.me" in resp.text
    assert client.get("/skill.md").status_code == 200


def test_pages_render(client):
    mission = create(client, kind="idea", title="mission", payload={"is_mission": True, "goal": "g"})
    fact = create(
        client, kind="fact", title="a fact", question="?", refs=[{"rel": "part_of", "to": mission["id"]}]
    )
    for path in (
        "/",
        "/items",
        "/missions",
        "/events",
        f"/missions/{mission['id']}",
        f"/items/{fact['id']}",
        f"/items/{fact['id']}/graph",
        f"/items/{fact['id']}/edit",
        "/new/fact",
        "/new/calculator",
    ):
        assert client.get(path).status_code == 200, path


def test_html_form_creates_an_item(client):
    resp = client.post(
        "/new/idea",
        data={
            "title": "from the browser",
            "p_markdown": "hello",
            "p_is_mission": "on",
            "p_goal": "ship it",
            "status": "open",
            "by": "dsa",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    item_id = resp.headers["location"].split("/")[-1]
    assert client.get(f"/api/v1/items/{item_id}").json()["payload"]["is_mission"] is True


def test_html_form_shows_validation_errors_instead_of_500(client):
    resp = client.post("/new/fact", data={"title": "bad", "p_data": "{not json"})
    assert resp.status_code == 200
    assert "not valid JSON" in resp.text
