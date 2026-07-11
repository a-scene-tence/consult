"""GET /api/clients 목록 엔드포인트 테스트 — 조종석 드롭다운 동적 로드용.

등록된 고객이 {id(정수), client_id('C-{id}'), name} 형태로 반환되는지(브리지 원천),
admin 전용(무토큰 401)인지 검증한다.
"""

from __future__ import annotations

from compute.ingest import sample_client_profile

PROFILE = sample_client_profile()


def _register(client, name: str) -> int:
    body = {
        "name": name,
        "trade_name": PROFILE["trade_name"],
        "industry": PROFILE["industry"],
        "district_type": PROFILE["district_type"],
        "location_raw": PROFILE["location_raw"],
        "owner_gender": PROFILE["owner_gender"],
        "owner_age": PROFILE["owner_age"],
        "owner_age_band": PROFILE["owner_age_band"],
        "risk_appetite": PROFILE["risk_appetite"],
    }
    r = client.post("/api/clients", json=body)
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def test_list_clients_returns_bridge_fields(client):
    id1 = _register(client, "김사장")
    id2 = _register(client, "이사장")

    r = client.get("/api/clients")
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 2

    by_id = {row["id"]: row for row in data}
    assert set(by_id) == {id1, id2}
    for row in data:
        assert row["client_id"] == f"C-{row['id']}"  # 라벨 파생 규칙 일관성
        assert "name" in row
        assert "trade_name" in row                    # 상호명 필드 존재(식별용)
        assert row["trade_name"] == PROFILE["trade_name"]
        assert row["report_token"] and len(row["report_token"]) == 32  # 공유 링크 토큰
    assert by_id[id1]["name"] == "김사장"


def test_list_clients_empty(client):
    r = client.get("/api/clients")
    assert r.status_code == 200
    assert r.json() == []


def test_list_clients_requires_auth(anon_client):
    assert anon_client.get("/api/clients").status_code == 401
