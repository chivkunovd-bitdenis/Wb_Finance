from app.database_url import normalize_database_url
import sqlalchemy


def test_bare_postgresql_url_uses_installed_psycopg2_driver():
    assert normalize_database_url("postgresql://user:pass@db:5432/finance") == (
        "postgresql+psycopg2://user:pass@db:5432/finance"
    )


def test_explicit_postgresql_driver_is_preserved():
    for url in (
        "postgresql+psycopg2://user:pass@db:5432/finance",
        "postgresql+psycopg://user:pass@db:5432/finance",
    ):
        assert normalize_database_url(url) == url


def test_bare_postgresql_url_preserves_encoded_credentials_and_query_bytes():
    url = "postgresql://user:p%40ss@db:5432/finance?sslmode=require&tag=a%2Fb"
    assert normalize_database_url(url) == (
        "postgresql+psycopg2://user:p%40ss@db:5432/finance?sslmode=require&tag=a%2Fb"
    )


def test_non_postgresql_url_is_preserved():
    url = "sqlite:///finance.db"
    assert normalize_database_url(url) == url


def test_api_startup_precheck_uses_normalized_postgresql_driver(monkeypatch):
    from docker_entrypoint_api import _db_version_rows

    captured = {}

    class Result:
        @staticmethod
        def fetchall():
            return [("head",)]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def execute(_statement):
            return Result()

    class Engine:
        @staticmethod
        def connect():
            return Connection()

    def fake_create_engine(url):
        captured["url"] = url
        return Engine()

    monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)

    assert _db_version_rows("postgresql://user:pass@db:5432/finance") == ["head"]
    assert captured["url"] == "postgresql+psycopg2://user:pass@db:5432/finance"
