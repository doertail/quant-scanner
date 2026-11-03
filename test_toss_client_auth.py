from toss_client import TossClient


class Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload
        self.text = str(payload)

    def json(self):
        return self.payload


def test_get_renews_revoked_token_once(monkeypatch):
    issued = iter(["old", "new"])
    sent = []
    client = TossClient("id", "secret")
    monkeypatch.setattr(client, "_get_token", lambda: next(issued))

    def fake_get(url, *, headers, params, timeout):
        sent.append(headers)
        if len(sent) == 1:
            return Response(401, {"error": {"code": "token-revoked"}})
        return Response(200, {"result": [{"symbol": "AKAM", "lastPrice": "113.5"}]})

    monkeypatch.setattr("toss_client.requests.get", fake_get)
    result = client.get_prices(["AKAM"])
    assert result["result"][0]["lastPrice"] == "113.5"
    assert [headers["Authorization"] for headers in sent] == ["Bearer old", "Bearer new"]


def test_get_does_not_retry_unrelated_unauthorized(monkeypatch):
    calls = []
    client = TossClient("id", "secret")
    monkeypatch.setattr(client, "_get_token", lambda: "token")

    def fake_get(*args, **kwargs):
        calls.append(1)
        return Response(401, {"error": {"code": "invalid-token"}})

    monkeypatch.setattr("toss_client.requests.get", fake_get)
    try:
        client.get_prices(["AKAM"])
    except Exception as exc:
        assert getattr(exc, "status", None) == 401
    else:
        assert False, "401 must remain visible"
    assert len(calls) == 1
