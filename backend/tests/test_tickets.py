"""Ärendelivscykel: nummer, terminalt closed, merge, CSAT."""

import re


async def _create(client, headers, customer_id, title="Ärende"):
    r = await client.post("/api/tickets", json={"customer_id": customer_id, "title": title}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def test_ticket_number_format_and_unique(client, admin_headers, customer_id):
    numbers = [(await _create(client, admin_headers, customer_id))["ticket_number"] for _ in range(3)]
    for n in numbers:
        assert re.match(r"^TF\d{8}-\d{4}$", n), n
    assert len(set(numbers)) == 3  # unika, ingen race-kollision


async def test_closed_is_terminal(client, admin_headers, customer_id):
    tk = await _create(client, admin_headers, customer_id)
    tid = tk["id"]
    assert (await client.put(f"/api/tickets/{tid}", json={"status": "resolved"}, headers=admin_headers)).status_code == 200
    assert (await client.put(f"/api/tickets/{tid}", json={"status": "closed"}, headers=admin_headers)).status_code == 200
    # Stängt ärende kan inte återöppnas
    r = await client.put(f"/api/tickets/{tid}", json={"status": "open"}, headers=admin_headers)
    assert r.status_code == 409


async def test_merge_moves_messages(client, admin_headers, customer_id):
    src = await _create(client, admin_headers, customer_id, "Källa")
    dst = await _create(client, admin_headers, customer_id, "Mål")
    await client.post(f"/api/tickets/{src['id']}/messages",
                      json={"body": "hej fran kallan", "is_internal": False}, headers=admin_headers)
    r = await client.post(f"/api/tickets/{src['id']}/merge",
                          json={"target_ticket_id": dst["id"]}, headers=admin_headers)
    assert r.status_code == 200, r.text
    target = r.json()
    bodies = " ".join(m["body"] for m in target["messages"])
    assert "hej fran kallan" in bodies
    # Källan är nu stängd och kopplad som child
    src_after = (await client.get(f"/api/tickets/{src['id']}", headers=admin_headers)).json()
    assert src_after["status"] == "closed"
    assert src_after["parent_ticket_id"] == dst["id"]


async def test_csat_requires_resolved(client, admin_headers, customer_id):
    tk = await _create(client, admin_headers, customer_id)
    tid = tk["id"]
    # Går inte att betygsätta ett öppet ärende
    r = await client.post(f"/api/tickets/{tid}/csat", json={"score": 5}, headers=admin_headers)
    assert r.status_code == 400
    await client.put(f"/api/tickets/{tid}", json={"status": "resolved"}, headers=admin_headers)
    r = await client.post(f"/api/tickets/{tid}/csat", json={"score": 5, "comment": "bra"}, headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["csat_score"] == 5
