import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://wb_finance:wb_finance@localhost:5432/wb_finance")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
