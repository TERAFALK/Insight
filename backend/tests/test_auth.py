"""Autentisering: login och lösenordsåterställning."""


async def test_login_success(client, admin_headers):
    # admin_headers innebär att login redan lyckats; verifiera att token duger
    r = await client.get("/api/auth/me", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


async def test_login_wrong_password(client, admin_headers):
    r = await client.post("/api/auth/token", data={"username": "admin@test.se", "password": "fel"})
    assert r.status_code == 401


async def test_forgot_password_always_ok(client):
    # Svarar ok även för okänd adress (ingen user-enumeration)
    r = await client.post("/api/auth/forgot-password", json={"email": "finns.inte@test.se"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_reset_password_bad_token(client):
    r = await client.post("/api/auth/reset-password", json={"token": "trasig", "password": "nyttlosen1"})
    assert r.status_code == 400
