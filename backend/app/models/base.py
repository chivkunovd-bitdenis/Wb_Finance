import uuid

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def uuid_gen():
    return str(uuid.uuid4())
