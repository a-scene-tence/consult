// 공통 fetch 헬퍼 (Phase 5). API 오류(detail)를 throw 한다.
async function apiGet(url) {
  const r = await fetch(url);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
  return d;
}

async function apiPost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
