import io
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from starlette.concurrency import run_in_threadpool

from .ai_analyzer import AIError, GeminiAnalyzer
from .config import ROOT, Settings
from .database import Database
from .marker_detector import detect_markers
from .models import Essay, EssayWork
from .schemas import EssayInput, MAX_SCORES, word_count
from .feedback import version_comparison
from .workflow import SECTIONS, pending_section, read_snapshot, snapshot, finish_section, claim_retry
from .accounts import current_user, admin_user, service
from .auth_models import EssayOwner
from .account_routes import install_accounts
from .mailer import SMTPMailer

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, analyzer=None, database=None, mailer=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        app.state.jobs = set()
        app.state.settings = settings
        app.state.mailer = mailer or SMTPMailer(settings)
        app.state.analyzer = analyzer or GeminiAnalyzer(settings)
        app.state.database = database
        if app.state.database is None and settings.database_url.get_secret_value():
            try:
                app.state.database = Database(settings.database_url.get_secret_value())
            except (ValueError, SQLAlchemyError):
                pass
        if app.state.database:
            try:
                await run_in_threadpool(app.state.database.initialize)
            except SQLAlchemyError:
                # Keep UI and diagnostics available; retry initialization on requests.
                pass
        yield
        for task in list(app.state.jobs):
            task.cancel()
        await asyncio.gather(*app.state.jobs, return_exceptions=True)
        if app.state.database:
            app.state.database.engine.dispose()

    app = FastAPI(title="tekSER API", lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            expected = settings.site_url.rstrip("/") or str(request.base_url).rstrip("/")
            if (request.headers.get("X-Requested-With") != "tekSER" or
                request.headers.get("origin", expected) != expected or
                request.headers.get("sec-fetch-site") == "cross-site"):
                return JSONResponse(status_code=403, content={"detail": "Сұрау көзі расталмады. Бетті жаңартыңыз."})
        if path.startswith("/api/essays") or path == "/api/ocr":
            try:
                request.state.user = await run_in_threadpool(current_user, request)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            except SQLAlchemyError:
                return JSONResponse(status_code=503, content={"detail": "Деректер базасына қосылу мүмкін болмады. Кейінірек қайталаңыз."})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        # The API documentation uses CDN assets; apply the strict policy to the app.
        if request.url.path in ("/", "/auth", "/profile", "/admin") or request.url.path.startswith("/static"):
            response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AIError)
    async def ai_error(request, error):
        return JSONResponse(status_code=error.status, content={"detail": error.message})

    @app.exception_handler(SQLAlchemyError)
    async def db_error(request, error):
        return JSONResponse(status_code=503, content={"detail": "Деректер базасына қосылу немесе нәтижені сақтау мүмкін болмады."})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        if request.url.path.startswith(("/api/auth", "/api/account", "/api/admin")):
            fields = {}
            for item in error.errors():
                key = str(item["loc"][-1]) if len(item["loc"]) > 1 else "form"
                message = item["msg"].removeprefix("Value error, ")
                if key in ("password", "new_password") and item["type"] == "string_too_short":
                    message = "Құпиясөз кемінде 4 таңба болуы керек."
                fields[key] = message
            return JSONResponse(status_code=422, content={"detail": {"message": "Енгізілген деректерді тексеріңіз.", "fields": fields}})
        return JSONResponse(status_code=422, content={"detail": "Деректерді тексеріңіз: тақырып 1–250 таңба, эссе 1–30000 таңба; нұсқа мен сұрау идентификаторлары жарамды болуы керек."})

    def get_db():
        if app.state.database is None:
            raise HTTPException(503, "PostgreSQL бапталмаған. Сервердегі DATABASE_URL айнымалысын тексеріңіз.")
        return app.state.database

    def ensure_db():
        db = get_db()
        db.initialize()
        db.ping()
        return db

    async def run_section(db, essay_id, name, section, title, essay, markers):
        result, error, cancelled = None, None, False
        try:
            async with asyncio.timeout(settings.gemini_timeout_seconds + 2):
                if name == "grade":
                    result = await app.state.analyzer.analyze(title, essay, markers)
                else:
                    result = await app.state.analyzer.compare_topic(title, essay)
        except asyncio.CancelledError:
            error, cancelled = AIError("Сервер тоқтағандықтан тексеру үзілді. Осы бөлімді қайта тексеріңіз.", 503), True
        except TimeoutError:
            error = AIError("Тексеру уақыты аяқталды. Осы бөлімді қайта тексеріңіз.", 504)
        except AIError as exc:
            error = exc
        except Exception as exc:
            logger.error("Analysis section failed (%s)", type(exc).__name__)
            error = AIError("Бұл бөлімді тексеру орындалмады. Дайын бөлімдер сақталды.")
        await run_in_threadpool(finish_section, db, essay_id, name, section["attempt"], result=result, error=error,
                                model=settings.gemini_model)
        if cancelled:
            raise asyncio.CancelledError

    def start_section(*args):
        task = asyncio.create_task(run_section(*args))
        app.state.jobs.add(task)
        def done(finished):
            app.state.jobs.discard(finished)
            if not finished.cancelled() and finished.exception():
                logger.error("Could not persist an analysis section; its lease will expire")
        task.add_done_callback(done)
        return task

    @app.get("/api/health")
    def health():
        db_ready = False
        try:
            ensure_db()
            db_ready = True
        except (HTTPException, SQLAlchemyError):
            pass
        key_ready = bool(settings.gemini_api_key.get_secret_value())
        return JSONResponse(status_code=200 if db_ready and key_ready else 503,
                            content={"database": db_ready, "gemini_configured": key_ready,
                                     "model": settings.gemini_model})

    @app.post("/api/essays/analyze", status_code=201)
    async def analyze(body: EssayInput, request: Request, background: bool = False):
        db = await run_in_threadpool(ensure_db)
        markers = detect_markers(body.essay_text)
        saved = {"analysis": None, "topic_alignment": None, "total_score": None, "annotations": [],
                 "max_scores": MAX_SCORES, "markers": markers, "model": settings.gemini_model,
                 "sections": {name: pending_section(settings.gemini_timeout_seconds) for name in SECTIONS}}
        def create_record():
            with db.session.begin() as session:
                if body.request_id:
                    previous = session.scalar(select(EssayWork).where(EssayWork.request_id == str(body.request_id)))
                    if previous:
                        service(request).assert_owner(request.state.user.id, previous.essay_id)
                        old = session.get(Essay, previous.essay_id)
                        if (old.title, old.essay_text, previous.parent_id) != (body.title, body.essay_text, body.parent_id):
                            raise HTTPException(409, "Бұл сұрау идентификаторы басқа мәтінге қолданылған.")
                        return old.id, False
                if body.parent_id and session.get(Essay, body.parent_id) is None:
                    raise HTTPException(404, "Алдыңғы эссе нұсқасы табылмады.")
                if body.parent_id:
                    service(request).assert_owner(request.state.user.id, body.parent_id)
                essay = Essay(title=body.title, essay_text=body.essay_text, word_count=word_count(body.essay_text))
                session.add(essay)
                session.flush()
                session.add(EssayOwner(essay_id=essay.id, user_id=request.state.user.id))
                session.add(EssayWork(essay_id=essay.id, parent_id=body.parent_id,
                                     request_id=str(body.request_id) if body.request_id else None, state=saved))
                return essay.id, True
        try:
            essay_id, created = await run_in_threadpool(create_record)
        except IntegrityError:
            if not body.request_id:
                raise
            essay_id, created = await run_in_threadpool(create_record)
        if created:
            tasks = [start_section(db, essay_id, name, saved["sections"][name], body.title, body.essay_text, markers) for name in SECTIONS]
            if not background:
                await asyncio.gather(*tasks)
        data = await run_in_threadpool(read_snapshot, db, essay_id)
        return JSONResponse(status_code=202 if data["status"] == "pending" else 201, content=data)

    @app.post("/api/essays/{essay_id}/retry/{section}")
    async def retry_section(essay_id: int, section: Literal["grade", "topic"], request: Request, background: bool = False):
        await run_in_threadpool(service(request).assert_owner, request.state.user.id, essay_id)
        db = await run_in_threadpool(ensure_db)
        claimed = await run_in_threadpool(claim_retry, db, essay_id, section, settings.gemini_timeout_seconds)
        if claimed["claimed"]:
            task = start_section(db, essay_id, section, claimed["section"], claimed["title"], claimed["essay_text"], claimed["markers"])
            if not background:
                await task
        data = await run_in_threadpool(read_snapshot, db, essay_id)
        return JSONResponse(status_code=202 if data["status"] == "pending" else 200, content=data)

    @app.get("/api/essays")
    def history(request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
        with ensure_db().session() as session:
            essays = session.scalars(select(Essay).join(EssayOwner).where(EssayOwner.user_id == request.state.user.id).order_by(Essay.id.desc()).offset(offset).limit(limit)).all()
            return [{key: snapshot(e)[key] for key in ("id", "title", "word_count", "total_score", "created_at", "status", "parent_id")} for e in essays]

    @app.get("/api/essays/{essay_id}")
    def detail(essay_id: int, request: Request):
        service(request).assert_owner(request.state.user.id, essay_id)
        return read_snapshot(ensure_db(), essay_id)

    @app.get("/api/essays/{essay_id}/versions")
    def versions(essay_id: int, request: Request):
        service(request).assert_owner(request.state.user.id, essay_id)
        db = ensure_db()
        current = read_snapshot(db, essay_id)
        result = [{key: current[key] for key in ("id", "title", "created_at", "total_score", "status")}]
        parent_id = current["parent_id"]
        while parent_id and len(result) < 100:
            service(request).assert_owner(request.state.user.id, parent_id)
            current = read_snapshot(db, parent_id)
            result.append({key: current[key] for key in ("id", "title", "created_at", "total_score", "status")})
            parent_id = current["parent_id"]
        return result

    @app.get("/api/essays/{essay_id}/comparison")
    def comparison(essay_id: int, request: Request, before_id: int | None = Query(None, gt=0)):
        service(request).assert_owner(request.state.user.id, essay_id)
        db = ensure_db()
        after = read_snapshot(db, essay_id)
        target = before_id or after["parent_id"]
        if not target:
            raise HTTPException(404, "Бұл эссенің алдыңғы нұсқасы жоқ.")
        service(request).assert_owner(request.state.user.id, target)
        parent_id, found = after["parent_id"], False
        for _ in range(100):
            if parent_id is None:
                break
            if parent_id == target:
                found = True
                break
            service(request).assert_owner(request.state.user.id, parent_id)
            parent_id = read_snapshot(db, parent_id)["parent_id"]
        if not found:
            raise HTTPException(422, "Салыстыру үшін осы эссенің алдыңғы нұсқасын таңдаңыз.")
        return version_comparison(read_snapshot(db, target), after)

    @app.delete("/api/essays/{essay_id}", status_code=204)
    def delete(essay_id: int, request: Request):
        service(request).assert_owner(request.state.user.id, essay_id)
        with ensure_db().session.begin() as session:
            essay = session.get(Essay, essay_id)
            if not essay:
                raise HTTPException(404, "Эссе табылмады.")
            session.delete(essay)

    @app.post("/api/ocr")
    async def ocr(request: Request, image: UploadFile = File()):
        data = await image.read(8 * 1024 * 1024 + 1)
        await image.close()
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(413, "Сурет көлемі 8 МБ-тан аспауы керек.")
        try:
            with Image.open(io.BytesIO(data)) as picture:
                if picture.format not in ("JPEG", "PNG", "WEBP") or picture.width * picture.height > 20_000_000:
                    raise HTTPException(422, "JPEG, PNG немесе WebP, 20 мегапиксельге дейінгі сурет таңдаңыз.")
                picture.verify()
                mime = Image.MIME[picture.format]
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise HTTPException(422, "Жарамды сурет жүктеңіз.") from None
        text = await app.state.analyzer.transcribe(data, mime)
        return {"text": text, "warning": "Қолжазбаны тану қателесуі мүмкін. Талдауға дейін мәтінді тексеріп, түзетіңіз."}

    install_accounts(app)
    app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")

    @app.get("/auth", include_in_schema=False)
    def account_page():
        return FileResponse(ROOT / "frontend" / "account.html", headers={"Cache-Control": "no-cache"})

    def guarded_page(request, admin=False):
        try:
            (admin_user if admin else current_user)(request)
        except HTTPException as exc:
            if exc.status_code == 401:
                return RedirectResponse("/auth?next=" + request.url.path, status_code=303)
            raise
        return FileResponse(ROOT / "frontend" / "account.html", headers={"Cache-Control": "no-store"})

    @app.get("/profile", include_in_schema=False)
    def profile_page(request: Request):
        return guarded_page(request)

    @app.get("/admin", include_in_schema=False)
    def admin_page(request: Request):
        return guarded_page(request, admin=True)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(ROOT / "frontend" / "index.html")

    return app


app = create_app()
