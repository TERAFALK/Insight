"""Behörigheter mellan kundanvändare och administratörer."""


async def test_customer_cannot_create_ticket_for_other_customer(client, admin_headers, customer_headers, customer_id):
    # Skapa en annan kund som admin
    r = await client.post("/api/customers", json={"name": "Annan kund"}, headers=admin_headers)
    other_id = r.json()["id"]
    # Kundanvändaren (kopplad till customer_id) försöker skapa ärende för annan kund
    r = await client.post(
        "/api/tickets",
        json={"customer_id": other_id, "title": "Otillåtet"},
        headers=customer_headers,
    )
    assert r.status_code == 403


async def test_customer_can_create_ticket_for_own_customer(client, customer_headers, customer_id):
    r = await client.post(
        "/api/tickets",
        json={"customer_id": customer_id, "title": "Mitt ärende"},
        headers=customer_headers,
    )
    assert r.status_code == 201, r.text


async def test_customer_cannot_delete_ticket(client, admin_headers, customer_headers, customer_id):
    r = await client.post(
        "/api/tickets", json={"customer_id": customer_id, "title": "Radera?"}, headers=admin_headers
    )
    ticket_id = r.json()["id"]
    r = await client.delete(f"/api/tickets/{ticket_id}", headers=customer_headers)
    assert r.status_code == 403
