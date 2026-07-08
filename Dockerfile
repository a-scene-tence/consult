# consult — 재무 컨설팅 자동화 시스템 (Phase 7)
# 웹 서버(FastAPI) / Celery 워커 공용 이미지.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 애플리케이션 소스 전체 복사 후 설치(setuptools 가 패키지 빌드에 소스를 요구).
COPY . .
RUN pip install --upgrade pip && pip install . && chmod +x /app/entrypoint.sh

EXPOSE 8000

# entrypoint 가 DB 대기 + alembic upgrade 를 수행한 뒤 CMD 를 exec 한다.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
