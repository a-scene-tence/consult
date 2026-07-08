# db/migrations/ — 레거시 원시 SQL (Phase 1~4)

이 디렉터리의 `0001~0003_*.sql` 은 Phase 1~4 에서 손으로 작성한 초기 DDL 이다.

**Phase 6 부터 스키마 마이그레이션의 단일 소스는 `alembic/` 이다.** `db/models.py`(Base.metadata)를
기준으로 `alembic revision --autogenerate` 로 변경을 관리한다.

```bash
# 최초/변경 반영
export DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/consult
alembic upgrade head

# 모델 변경 후 새 리비전 자동 생성
alembic revision --autogenerate -m "describe change"

# 모델과 DB 스키마 정합성 점검
alembic check
```

원시 SQL 파일은 참고용으로 남겨두되, 신규 변경은 반드시 Alembic 으로 수행한다.
