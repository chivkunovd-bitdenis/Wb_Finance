import uuid
from sqlalchemy import Column, DateTime, func
from sqlalchemy.dialects.postgresql import UUID

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def uuid_gen():
    return str(uuid.uuid4())
