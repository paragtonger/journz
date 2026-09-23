from datetime import UTC, datetime

from sqlmodel import Field, Relationship, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class JournalTag(SQLModel, table=True):
    """Link table between journals and tags."""

    journal_id: int | None = Field(
        default=None, foreign_key="journal.id", primary_key=True
    )
    tag_id: int | None = Field(default=None, foreign_key="tag.id", primary_key=True)


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    password_hash: str
    display_name: str = Field(default="Writer")
    theme_preference: str = Field(default="system")
    is_deleted: bool = Field(default=False, index=True)
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    journals: list["Journal"] = Relationship(back_populates="user")
    sessions: list["Session"] = Relationship(back_populates="user")


class Session(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(index=True, unique=True)
    expires_at: datetime
    created_at: datetime = Field(default_factory=utc_now)
    last_used_at: datetime = Field(default_factory=utc_now)

    user: User = Relationship(back_populates="sessions")


class Journal(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = Field(default=None, foreign_key="user.id", index=True)
    title: str = Field(default="")
    content: str = Field(default="")
    content_format: str = Field(default="plain_text")
    version: int = Field(default=1)
    is_deleted: bool = Field(default=False, index=True)
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    user: User | None = Relationship(back_populates="journals")
    tags: list["Tag"] = Relationship(back_populates="journals", link_model=JournalTag)


class Tag(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    name: str = Field(index=True)
    is_deleted: bool = Field(default=False, index=True)
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)

    journals: list[Journal] = Relationship(back_populates="tags", link_model=JournalTag)
