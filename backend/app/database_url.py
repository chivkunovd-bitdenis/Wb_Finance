def normalize_database_url(database_url: str) -> str:
    """Pin bare PostgreSQL URLs to the installed psycopg2 driver.

    SQLAlchemy 2.1 resolves ``postgresql://`` to psycopg (v3), while this
    project installs psycopg2. Preserve explicit drivers and all URL bytes
    after the leading scheme, including encoded credentials and query values.
    """
    if not database_url.startswith("postgresql://"):
        return database_url
    return "postgresql+psycopg2://" + database_url[len("postgresql://") :]
