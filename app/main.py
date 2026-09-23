import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Any, cast

from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from .auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    create_session,
    get_current_user,
    normalize_email,
    password_hash,
    validate_csrf,
)
from .config import get_settings
from .db import get_session, init_db
from .models import Journal, Tag, User, utc_now

settings = get_settings()
templates = Jinja2Templates(directory="app/templates")
SessionDep = Annotated[Session, Depends(get_session)]
MAX_TITLE_LENGTH = 200
MAX_CONTENT_LENGTH = 100_000
MAX_DISPLAY_NAME_LENGTH = 80
MAX_TAGS = 10


def sync_journal_tags(
    journal: Journal,
    raw_names: str,
    user_id: int,
    session: Session,
) -> None:
    names = []
    seen = set()
    for raw_name in raw_names.split(",")[:MAX_TAGS]:
        name = " ".join(raw_name.split()).strip()
        key = name.casefold()
        if name and key not in seen:
            seen.add(key)
            names.append(name[:80])

    existing = session.exec(select(Tag).where(Tag.user_id == user_id)).all()
    by_name = {tag.name.casefold(): tag for tag in existing}
    journal.tags = [
        by_name.setdefault(name.casefold(), Tag(user_id=user_id, name=name))
        for name in names
    ]
    for tag in journal.tags:
        session.add(tag)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def csrf_cookie(request: Request, call_next: Any):
    token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
    request.state.csrf_token = token
    response = await call_next(request)
    if request.cookies.get(CSRF_COOKIE) != token:
        response.set_cookie(
            CSRF_COOKIE,
            token,
            httponly=False,
            secure=request.url.scheme == "https",
            samesite="lax",
        )
    return response


@app.get("/", name="dashboard")
async def dashboard(
    request: Request,
    session: SessionDep,
    q: str = Query(default="", max_length=200),
    tag: str = Query(default="", max_length=80),
    sort: str = Query(default="updated_desc"),
    page: int = Query(default=1, ge=1),
):
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse("/login", status_code=303)
    query = select(Journal).where(
        Journal.user_id == user.id, Journal.is_deleted == False
    )
    if q.strip():
        pattern = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(Journal.title).like(pattern),
                func.lower(Journal.content).like(pattern),
            )
        )
    if tag.strip():
        query = query.join(cast(Any, Journal.tags)).where(
            Tag.user_id == user.id,
            func.lower(Tag.name) == tag.strip().lower(),
        )
    order_by = {
        "updated_asc": cast(Any, Journal.updated_at).asc(),
        "title_asc": cast(Any, Journal.title).asc(),
        "title_desc": cast(Any, Journal.title).desc(),
    }.get(sort, cast(Any, Journal.updated_at).desc())
    page_size = 20
    journals = session.exec(
        query.order_by(order_by).offset((page - 1) * page_size).limit(page_size + 1)
    ).all()
    has_next_page = len(journals) > page_size
    journals = journals[:page_size]
    tags = session.exec(
        select(Tag)
        .where(Tag.user_id == user.id, Tag.is_deleted == False)
        .order_by(Tag.name)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "app_name": settings.app_name,
            "user": user,
            "journals": journals,
            "tags": tags,
            "query": q,
            "selected_tag": tag,
            "selected_sort": sort,
            "page": page,
            "has_next_page": has_next_page,
        },
    )


@app.get("/trash", response_class=HTMLResponse)
async def trash(request: Request, session: SessionDep):
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse("/login", status_code=303)
    journals = session.exec(
        select(Journal)
        .where(Journal.user_id == user.id, Journal.is_deleted == True)
        .order_by(col(Journal.updated_at).desc())
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="trash.html",
        context={"app_name": settings.app_name, "user": user, "journals": journals},
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session: SessionDep):
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"app_name": settings.app_name, "user": user},
    )


@app.post("/settings")
async def update_settings(
    request: Request,
    session: SessionDep,
    csrf_token: str = Form(),
    theme_preference: str = Form("system"),
):
    validate_csrf(request, csrf_token)
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if theme_preference not in {"system", "light", "dark"}:
        return HTMLResponse("Invalid theme preference.", status_code=422)
    user.theme_preference = theme_preference
    user.updated_at = utc_now()
    session.add(user)
    session.commit()
    return RedirectResponse("/settings", status_code=303)


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="register.html", context={"app_name": settings.app_name}
    )


@app.post("/register")
async def register(
    request: Request,
    session: SessionDep,
    csrf_token: str = Form(),
    email: str = Form(),
    password: str = Form(),
    display_name: str = Form("Writer"),
):
    validate_csrf(request, csrf_token)
    email = normalize_email(email)
    display_name = display_name.strip()
    if (
        len(password) < 8
        or len(password) > 128
        or not email
        or len(display_name) > MAX_DISPLAY_NAME_LENGTH
    ):
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "app_name": settings.app_name,
                "error": "Use a valid email and a password of at least 8 characters.",
            },
            status_code=400,
        )
    if session.exec(select(User).where(User.email == email)).first():
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "app_name": settings.app_name,
                "error": "An account with that email already exists.",
            },
            status_code=400,
        )
    user = User(
        email=email,
        password_hash=password_hash.hash(password),
        display_name=display_name or "Writer",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    token = create_session(user, session)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
    )
    return response


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="login.html", context={"app_name": settings.app_name}
    )


@app.post("/login")
async def login(
    request: Request,
    session: SessionDep,
    csrf_token: str = Form(),
    email: str = Form(),
    password: str = Form(),
):
    validate_csrf(request, csrf_token)
    user = session.exec(
        select(User).where(User.email == normalize_email(email))
    ).first()
    if not user or not password_hash.verify(password, user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "app_name": settings.app_name,
                "error": "Email or password is incorrect.",
            },
            status_code=400,
        )
    token = create_session(user, session)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
    )
    return response


@app.post("/logout")
async def logout(request: Request, csrf_token: str = Form()):
    validate_csrf(request, csrf_token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.post("/journals")
async def create_journal(
    request: Request,
    session: SessionDep,
    csrf_token: str = Form(),
    content: str = Form(),
):
    validate_csrf(request, csrf_token)
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse("/login", status_code=303)
    content = content.strip()
    if not content:
        return RedirectResponse("/", status_code=303)
    if len(content) > MAX_CONTENT_LENGTH:
        return HTMLResponse("Journal content is too long.", status_code=422)
    journal = Journal(user_id=user.id, content=content)
    session.add(journal)
    session.commit()
    return RedirectResponse("/", status_code=303)


@app.get("/journals/{journal_id}", response_class=HTMLResponse)
async def journal_detail(
    request: Request,
    session: SessionDep,
    journal_id: int,
):
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse("/login", status_code=303)
    journal = session.exec(
        select(Journal).where(
            Journal.id == journal_id,
            Journal.user_id == user.id,
            Journal.is_deleted == False,
        )
    ).first()
    if not journal:
        return HTMLResponse("Journal not found.", status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="journal.html",
        context={
            "app_name": settings.app_name,
            "user": user,
            "journal": journal,
            "tag_names": ", ".join(tag.name for tag in journal.tags),
        },
    )


@app.post("/journals/{journal_id}")
async def update_journal(
    request: Request,
    session: SessionDep,
    journal_id: int,
    csrf_token: str = Form(),
    title: str = Form(""),
    content: str = Form(""),
    tag_names: str = Form(""),
    version: int = Form(),
):
    validate_csrf(request, csrf_token)
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse("/login", status_code=303)
    journal = session.exec(
        select(Journal).where(
            Journal.id == journal_id,
            Journal.user_id == user.id,
            Journal.is_deleted == False,
        )
    ).first()
    if not journal:
        return HTMLResponse("Journal not found.", status_code=404)
    if version != journal.version:
        return templates.TemplateResponse(
            request=request,
            name="journal.html",
            context={
                "app_name": settings.app_name,
                "user": user,
                "journal": journal,
                "tag_names": ", ".join(tag.name for tag in journal.tags),
                "error": "This journal changed on another device. Reload before saving.",
            },
            status_code=409,
        )
    title = title.strip()
    if len(title) > MAX_TITLE_LENGTH or len(content) > MAX_CONTENT_LENGTH:
        return HTMLResponse("Journal title or content is too long.", status_code=422)
    journal.title = title
    journal.content = content
    assert user.id is not None
    sync_journal_tags(journal, tag_names, user.id, session)
    journal.version += 1
    journal.updated_at = utc_now()
    session.add(journal)
    session.commit()
    return RedirectResponse(f"/journals/{journal_id}", status_code=303)


@app.post("/journals/{journal_id}/delete")
async def delete_journal(
    request: Request,
    session: SessionDep,
    journal_id: int,
    csrf_token: str = Form(),
):
    validate_csrf(request, csrf_token)
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse("/login", status_code=303)
    journal = session.exec(
        select(Journal).where(Journal.id == journal_id, Journal.user_id == user.id)
    ).first()
    if not journal:
        return HTMLResponse("Journal not found.", status_code=404)
    journal.is_deleted = True
    journal.deleted_at = utc_now()
    journal.updated_at = utc_now()
    session.add(journal)
    session.commit()
    return RedirectResponse("/trash", status_code=303)


@app.post("/journals/{journal_id}/restore")
async def restore_journal(
    request: Request,
    session: SessionDep,
    journal_id: int,
    csrf_token: str = Form(),
):
    validate_csrf(request, csrf_token)
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse("/login", status_code=303)
    journal = session.exec(
        select(Journal).where(
            Journal.id == journal_id,
            Journal.user_id == user.id,
            Journal.is_deleted == True,
        )
    ).first()
    if not journal:
        return HTMLResponse("Journal not found.", status_code=404)
    journal.is_deleted = False
    journal.deleted_at = None
    journal.updated_at = utc_now()
    session.add(journal)
    session.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/journals/{journal_id}/permanent-delete")
async def permanently_delete_journal(
    request: Request,
    session: SessionDep,
    journal_id: int,
    csrf_token: str = Form(),
):
    validate_csrf(request, csrf_token)
    user = get_current_user(request, session)
    if not user:
        return RedirectResponse("/login", status_code=303)
    journal = session.exec(
        select(Journal).where(
            Journal.id == journal_id,
            Journal.user_id == user.id,
            Journal.is_deleted == True,
        )
    ).first()
    if not journal:
        return HTMLResponse("Journal not found.", status_code=404)
    session.delete(journal)
    session.commit()
    return RedirectResponse("/trash", status_code=303)


@app.put("/journals/{journal_id}/autosave")
async def autosave_journal(
    request: Request,
    session: SessionDep,
    journal_id: int,
):
    validate_csrf(request)
    user = get_current_user(request, session)
    if not user:
        return JSONResponse({"error": "Authentication required."}, status_code=401)
    payload = await request.json()
    title = payload.get("title", "")
    content = payload.get("content", "")
    version = payload.get("version")
    if (
        not isinstance(title, str)
        or not isinstance(content, str)
        or not isinstance(version, int)
    ):
        return JSONResponse({"error": "Invalid autosave payload."}, status_code=422)
    journal = session.exec(
        select(Journal).where(
            Journal.id == journal_id,
            Journal.user_id == user.id,
            Journal.is_deleted == False,
        )
    ).first()
    if not journal:
        return JSONResponse({"error": "Journal not found."}, status_code=404)
    if version != journal.version:
        return JSONResponse(
            {"error": "Journal changed on another device.", "version": journal.version},
            status_code=409,
        )
    if len(title) > MAX_TITLE_LENGTH or len(content) > MAX_CONTENT_LENGTH:
        return JSONResponse(
            {"error": "Journal content or title is too long."}, status_code=422
        )
    journal.title = title.strip()
    journal.content = content
    journal.version += 1
    journal.updated_at = utc_now()
    session.add(journal)
    session.commit()
    return {
        "saved": True,
        "version": journal.version,
        "updated_at": journal.updated_at.isoformat(),
    }
