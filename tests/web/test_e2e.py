"""E2E 통합 테스트 — 등록→ingest→start→검토→feedback→approve→dashboard 전 워크플로 HTTP 검증.

fake 에이전트로 실제 Claude 호출 없이, FastAPI TestClient 로 상태 머신 전 구간이 HTTP 단위로
흐르는지 검증한다. 백그라운드 태스크는 TestClient 가 동기 완료한다.
"""

from __future__ import annotations

from compute.ingest import sample_bs_raw, sample_client_profile, sample_pl_raw

PROFILE = sample_client_profile()


def _register(client) -> int:
    body = {
        "name": "김사장",
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
    assert r.status_code == 201
    return r.json()["client_id"]


def _ingest(client, client_id: int) -> int:
    r = client.post("/api/consulting/ingest", json={
        "client_id": client_id, "period": "2025-Q3",
        "pl_raw": sample_pl_raw(), "bs_raw": sample_bs_raw(),
    })
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "computed"
    return r.json()["draft_id"]


def test_full_workflow(client):
    # 1) 고객 등록
    client_id = _register(client)

    # 2) Master 업로드 → compute → computed
    draft_id = _ingest(client, client_id)

    # 3) 분석 시작(A1~A3, 백그라운드 동기 완료) → review_pending
    r = client.post(f"/api/consulting/{draft_id}/start")
    assert r.status_code == 202
    view = client.get(f"/api/consulting/{draft_id}").json()
    assert view["status"] == "review_pending"
    assert view["contradiction_flags"], "모순 플래그가 초안에 노출되어야 한다"
    assert view["payload"]["sections"]
    assert view["financials_pl"]["bep"]["daily_target_qty"] == 35
    assert view["financials_bs"]["cash_flow"]["cash_runway_months"] == 2.7

    # 4) 전문가 피드백 → A4 재작성(백그라운드) → version 2, 대시보드/권고 포함
    r = client.post(f"/api/consulting/{draft_id}/feedback",
                    json={"reviewer": "세무 파트너", "overall_note": "톤 부드럽게", "instructions": []})
    assert r.status_code == 202
    view = client.get(f"/api/consulting/{draft_id}").json()
    assert view["version"] == 2
    assert "dashboard_payload" in view["payload"]
    assert view["payload"]["recommendations"][0]["rec_code"] == "R-2025Q3-01"

    # 5) 승인·발행 → published + 대시보드 페이로드
    r = client.post(f"/api/consulting/{draft_id}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"
    hero_keys = {k["key"] for k in r.json()["dashboard_payload"]["hero_kpis"]}
    assert {"bep_attainment", "daily_target_qty"} <= hero_keys

    # 6) 고객 대시보드 조회
    r = client.get(f"/api/dashboard/{client_id}")
    assert r.status_code == 200
    dash = r.json()
    assert dash["client"]["name"] == "테스트 가게"
    hero_keys = {k["key"] for k in dash["hero_kpis"]}
    assert {"bep_attainment", "daily_target_qty"} <= hero_keys


def test_dashboard_before_publish_404(client):
    client_id = _register(client)
    _ingest(client, client_id)
    assert client.get(f"/api/dashboard/{client_id}").status_code == 404


def test_unknown_draft_404(client):
    assert client.get("/api/consulting/99999").status_code == 404
    assert client.post("/api/consulting/99999/start").status_code == 404


def test_illegal_transition_409(client):
    client_id = _register(client)
    draft_id = _ingest(client, client_id)
    assert client.post(f"/api/consulting/{draft_id}/start").status_code == 202
    # 이미 review_pending → 재-start 는 잘못된 전이(409)
    assert client.post(f"/api/consulting/{draft_id}/start").status_code == 409
    # computed/review_pending 에서 곧바로 approve(publish 대상 없음)도 거부
    assert client.post(f"/api/consulting/{draft_id}/approve").status_code == 409


def test_bad_ingest_400(client):
    client_id = _register(client)
    # 잘못된 Master(빈 dict) → compute ValueError → 400
    r = client.post("/api/consulting/ingest", json={
        "client_id": client_id, "period": "2025-Q3", "pl_raw": {}, "bs_raw": {},
    })
    assert r.status_code == 400
