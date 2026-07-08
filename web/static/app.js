// 공통 fetch 헬퍼 (Phase 5·6). JWT 토큰(localStorage)을 자동 부착하고 API 오류(detail)를 throw 한다.
function getToken() { return localStorage.getItem("consult_token"); }
function setToken(t) { localStorage.setItem("consult_token", t); }

function authHeaders(extra) {
  const h = Object.assign({}, extra || {});
  const t = getToken();
  if (t) h["Authorization"] = "Bearer " + t;
  return h;
}

// OAuth2 password flow 로그인 → 토큰 저장.
async function login(username, password) {
  const body = new URLSearchParams({ username, password });
  const r = await fetch("/api/auth/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
  setToken(d.access_token);
  return d;
}

async function apiGet(url) {
  const r = await fetch(url, { headers: authHeaders() });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
  return d;
}

async function apiPost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body || {}),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
  return d;
}

function el(id) {
  return document.getElementById(id);
}

// JSON 텍스트에어리어 안전 파싱.
function parseJSON(text, label) {
  try {
    return JSON.parse(text);
  } catch (e) {
    throw new Error(`${label} JSON 파싱 실패: ${e.message}`);
  }
}
