import json


async def signup(client, email: str, password: str = "password123") -> dict:
    r = await client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def upload(client, headers, name: str, content: bytes, mime: str = "application/octet-stream"):
    return await client.post("/documents", headers=headers, files={"file": (name, content, mime)})


def parse_sse(body: str) -> list[tuple[str, object]]:
    events = []
    for block in body.strip().split("\n\n"):
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if event:
            events.append((event, data))
    return events
