import io
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from .ai_analyzer import AIError, GeminiAnalyzer
from .config import ROOT, Settings
from .database import Database
from .marker_detector import detect_markers
from .models import Essay, EssayAnalysis
from .schemas import EssayInput, MAX_SCORES, word_count


def create_app(settings: Settings | None = None, analyzer=None, database=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
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
        if app.state.database:
            app.state.database.engine.dispose()

    app = FastAPI(title="Эссе Тексеру API", lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        # The API documentation uses CDN assets; apply the strict policy to the app.
        if request.url.path == "/" or request.url.path.startswith("/static"):
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
        return JSONResponse(status_code=422, content={"detail": "Деректерді тексеріңіз: тақырып 1–250 таңба, эссе 1–30000 таңба болуы керек."})

    def get_db():
        if app.state.database is None:
            raise HTTPException(503, "PostgreSQL бапталмаған. Сервердегі DATABASE_URL айнымалысын тексеріңіз.")
        return app.state.database

    def ensure_db():
        db = get_db()
        db.initialize()
        db.ping()
        return db

    def serialize(essay):
        return {"id": essay.id, "title": essay.title, "essay_text": essay.essay_text,
                "word_count": essay.word_count, "created_at": essay.created_at.isoformat(),
                **essay.analysis.analysis_result}

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
    async def analyze(body: EssayInput):
        db = await run_in_threadpool(ensure_db)
        markers = detect_markers(body.essay_text)
        result, topic = await app.state.analyzer.assess(body.title, body.essay_text, markers)
        scores = {name: getattr(result.criteria, name).score for name in MAX_SCORES}
        saved = {"analysis": result.model_dump(), "topic_alignment": topic.model_dump(), "total_score": sum(scores.values()),
                 "max_scores": MAX_SCORES, "markers": markers, "model": settings.gemini_model}

        def persist():
            with db.session.begin() as session:
                essay = Essay(title=body.title, essay_text=body.essay_text, word_count=word_count(body.essay_text))
                essay.analysis = EssayAnalysis(essay_style=result.classification.style,
                    essay_type=result.classification.essay_type, total_score=saved["total_score"],
                    analysis_result=saved, **{f"{name}_score": score for name, score in scores.items()})
                session.add(essay)
                session.flush()
                return serialize(essay)

        return await run_in_threadpool(persist)

    @app.get("/api/essays")
    def history(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
        with ensure_db().session() as session:
            essays = session.scalars(select(Essay).order_by(Essay.id.desc()).offset(offset).limit(limit)).all()
            return [{"id": e.id, "title": e.title, "word_count": e.word_count,
                     "total_score": e.analysis.total_score, "created_at": e.created_at.isoformat()} for e in essays]

    @app.get("/api/essays/{essay_id}")
    def detail(essay_id: int):
        with ensure_db().session() as session:
            essay = session.get(Essay, essay_id)
            if not essay:
                raise HTTPException(404, "Эссе табылмады.")
            return serialize(essay)

    @app.delete("/api/essays/{essay_id}", status_code=204)
    def delete(essay_id: int):
        with ensure_db().session.begin() as session:
            essay = session.get(Essay, essay_id)
            if not essay:
                raise HTTPException(404, "Эссе табылмады.")
            session.delete(essay)

    @app.post("/api/ocr")
    async def ocr(image: UploadFile = File()):
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

    app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(ROOT / "frontend" / "index.html")

    return app


app = create_app()
