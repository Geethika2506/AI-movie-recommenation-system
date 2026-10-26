import hashlib
import json
import logging
import secrets
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread
from typing import Annotated, Any, Literal, cast

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.account import (
    apply_change,
    new_verification_code,
    prepare_change,
    store_challenge,
    take_challenge,
)
from backend.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    verify_password,
)
from backend.cache import RecommendationCache
from backend.catalog import import_prepared_catalog
from backend.config import Settings, get_settings
from backend.db import Base, make_engine, make_session_factory, session_dependency
from backend.genres import movie_has_genre
from backend.group_sessions import consensus_reached, rank_group_session
from backend.logging_config import configure_logging
from backend.mail import VerificationDeliveryError, deliver_verification_code
from backend.mlops import assign_experiment
from backend.models import (
    Group,
    GroupInvite,
    GroupMember,
    GroupRoom,
    GroupRoomMember,
    GroupSatisfaction,
    GroupSession,
    GroupVote,
    Interaction,
    InteractionKind,
    ModelRun,
    Movie,
    MovieEmbedding,
    PreferenceEvent,
    Profile,
    Rating,
    RecommendationFeedback,
    RecommendationImpression,
    RefreshToken,
    StreamingAvailability,
    User,
    UserMovieState,
)
from backend.personal import (
    apply_onboarding_preferences,
    embedding_hybrid_recommendations,
    embedding_versions,
    rebuild_taste_profile,
)
from backend.posters import enrich_wikipedia_posters, hydrate_posters
from backend.recommendations import (
    discovery_for_profile,
    group_recommendations,
    similar_movies,
)
from backend.schemas import (
    AccountDelete,
    AuthOut,
    GroupCreate,
    GroupFinalizeIn,
    GroupJoinOut,
    GroupMemberOut,
    GroupOut,
    GroupRankingRequest,
    GroupRequest,
    GroupRoomMemberOut,
    GroupRoomOut,
    GroupSatisfactionIn,
    GroupSessionCreate,
    GroupSessionOut,
    GroupSessionRankingUpdate,
    GroupVoteIn,
    GroupVoteOut,
    InteractionIn,
    InviteCreate,
    InviteJoin,
    InviteOut,
    LegacyProfileClaim,
    ModelRunOut,
    MovieOut,
    MoviePage,
    OnboardingPreferencesIn,
    PreferenceStateOut,
    PreferenceUndo,
    PreferenceUpdate,
    ProfileCreate,
    ProfileOut,
    RatingIn,
    RatingOut,
    RecommendationOut,
    RoomInviteLinkOut,
    SystemMetadataOut,
    UserLogin,
    UserOut,
    UserRegister,
    UserSettingsUpdate,
    VerificationConfirm,
    VerificationIssued,
    VerificationRequest,
)
from recommender.contextual_bandit import (
    BanditAction,
    BanditDecision,
    VWPolicy,
    reward_for,
)

logger = logging.getLogger("cinematch")
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_session_sockets: dict[str, set[WebSocket]] = {}
_rate_windows: defaultdict[str, deque[float]] = defaultdict(deque)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.secure_cookies and settings.jwt_secret.startswith("development-only"):
        raise ValueError("production cookie mode requires a non-default JWT secret")
    configure_logging(settings.log_level)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        if settings.auto_create_schema:
            Base.metadata.create_all(engine)
        imported = import_prepared_catalog(factory, settings.data_dir)
        if imported:
            logger.info("catalog_imported", extra={"movies": imported})
        if settings.poster_lookup:
            Thread(
                target=enrich_wikipedia_posters,
                args=(factory, settings.data_dir),
                daemon=True,
                name="poster-backfill",
            ).start()
        fastapi_app.state.bandit_policy = None
        fastapi_app.state.redis = (
            Redis.from_url(settings.redis_url, decode_responses=True)
            if settings.redis_url
            else None
        )
        if settings.bandit_enabled:
            try:
                fastapi_app.state.bandit_policy = VWPolicy(
                    settings.artifact_dir / "vw" / "policy.model",
                    epsilon=settings.bandit_epsilon,
                )
            except (OSError, RuntimeError, ValueError):
                logger.exception("bandit_policy_unavailable")
        yield
        if fastapi_app.state.bandit_policy:
            fastapi_app.state.bandit_policy.close()
        if fastapi_app.state.redis:
            await fastapi_app.state.redis.aclose()
        fastapi_app.state.recommendation_cache.close()
        engine.dispose()

    app = FastAPI(title="CineMatch API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.recommendation_cache = RecommendationCache(
        ttl_seconds=settings.recommendation_cache_seconds,
        max_entries=settings.recommendation_cache_entries,
        redis_url=settings.redis_url,
    )
    frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    app.state.frontend_dist = frontend_dist
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _register_middleware(app, settings)
    _register_routes(app, factory, settings)
    if frontend_dist.is_dir():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")
    return app


def _register_middleware(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def request_controls(request: Request, call_next: object) -> Response:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        client = request.client.host if request.client else "unknown"
        category = (
            "auth"
            if request.url.path.startswith("/api/auth/")
            or request.url.path.startswith("/api/users/me/verification")
            else "api"
        )
        limit = (
            settings.auth_rate_limit_per_minute
            if category == "auth"
            else settings.rate_limit_per_minute
        )
        now = time.monotonic()
        exceeded = False
        redis_client: Redis | None = getattr(request.app.state, "redis", None)
        if redis_client:
            try:
                redis_key = f"rate:{category}:{client}:{int(time.time() // 60)}"
                count = await redis_client.incr(redis_key)
                if count == 1:
                    await redis_client.expire(redis_key, 65)
                exceeded = count > limit
            except RedisError:
                logger.exception("redis_rate_limit_unavailable")
                redis_client = None
        if not redis_client:
            window = _rate_windows[f"{id(request.app)}:{client}:{category}"]
            while window and window[0] <= now - 60:
                window.popleft()
            exceeded = len(window) >= limit
            window.append(now)
        if exceeded:
            return Response(
                '{"detail":"rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
                headers={"x-request-id": request_id, "retry-after": "60"},
            )
        if settings.read_only and request.method in MUTATING_METHODS:
            return Response(
                '{"detail":"service is in read-only mode"}',
                status_code=status.HTTP_403_FORBIDDEN,
                media_type="application/json",
                headers={"x-request-id": request_id},
            )
        started = time.perf_counter()
        response: Response = await call_next(request)  # type: ignore[operator]
        response.headers["x-request-id"] = request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "strict-origin-when-cross-origin"
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "request_id": request_id,
            },
        )
        return response


def _register_routes(app: FastAPI, factory: sessionmaker[Session], settings: Settings) -> None:
    def get_session() -> object:
        yield from session_dependency(factory)

    DB = Annotated[Session, Depends(get_session)]

    @app.get("/", response_model=None)
    def root(request: Request) -> FileResponse | dict[str, str]:
        index = request.app.state.frontend_dist / "index.html"
        if index.is_file():
            return FileResponse(index)
        return {
            "service": "cinematch-api",
            "health": "/health",
            "docs": "/docs",
            "app": settings.public_app_url,
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready(db: DB) -> dict[str, str]:
        try:
            db.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {"status": "ready"}

    @app.post("/api/auth/register", response_model=AuthOut, status_code=201)
    def register(payload: UserRegister, response: Response, db: DB) -> AuthOut:
        email = str(payload.email).lower()
        username = payload.username.strip()
        existing = db.scalar(
            select(User).where(
                (func.lower(User.email) == email) | (func.lower(User.username) == username.lower())
            )
        )
        if existing:
            raise HTTPException(status_code=409, detail="email or username already registered")
        user = User(
            username=username,
            email=email,
            password_hash=hash_password(payload.password),
        )
        db.add(user)
        db.flush()
        db.add(
            Profile(
                user_id=user.id,
                name=payload.profile_name.strip(),
                owner_key_hash=f"user:{user.id}",
            )
        )
        token, expires_at = create_access_token(user.id, settings)
        _issue_refresh_token(db, response, user.id, settings)
        db.commit()
        db.refresh(user)
        return AuthOut(
            access_token=token,
            expires_at=expires_at,
            user=UserOut.model_validate(user),
        )

    @app.post("/api/auth/login", response_model=AuthOut)
    def login(payload: UserLogin, response: Response, db: DB) -> AuthOut:
        user = db.scalar(select(User).where(func.lower(User.email) == str(payload.email).lower()))
        if (
            not user
            or not user.is_active
            or not verify_password(payload.password, user.password_hash)
        ):
            raise HTTPException(status_code=401, detail="invalid email or password")
        token, expires_at = create_access_token(user.id, settings)
        _issue_refresh_token(db, response, user.id, settings)
        db.commit()
        return AuthOut(
            access_token=token,
            expires_at=expires_at,
            user=UserOut.model_validate(user),
        )

    @app.post("/api/auth/refresh", response_model=AuthOut)
    def refresh_access(request: Request, response: Response, db: DB) -> AuthOut:
        raw = request.cookies.get("cinematch_refresh", "")
        record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
        now = datetime.now(UTC)
        if not record or record.revoked_at or _as_utc(record.expires_at) <= now:
            raise HTTPException(status_code=401, detail="invalid or expired refresh token")
        user = db.get(User, record.user_id)
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="account unavailable")
        record.revoked_at = now
        token, expires_at = create_access_token(user.id, settings)
        _issue_refresh_token(db, response, user.id, settings)
        db.commit()
        return AuthOut(
            access_token=token,
            expires_at=expires_at,
            user=UserOut.model_validate(user),
        )

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request, response: Response, db: DB) -> Response:
        raw = request.cookies.get("cinematch_refresh", "")
        record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
        if record and not record.revoked_at:
            record.revoked_at = datetime.now(UTC)
            db.commit()
        _clear_refresh_cookie(response, settings)
        response.status_code = 204
        return response

    @app.get("/api/users/me", response_model=UserOut)
    def current_user(request: Request, db: DB) -> User:
        return _required_user(db, request)

    @app.patch("/api/users/me", response_model=UserOut)
    def update_current_user(payload: UserSettingsUpdate, request: Request, db: DB) -> User:
        user = _required_user(db, request)
        user.country = payload.country
        user.streaming_provider_ids = payload.streaming_provider_ids
        db.commit()
        db.refresh(user)
        return user

    @app.post("/api/users/me/verification", response_model=VerificationIssued)
    def request_account_verification(
        payload: VerificationRequest, request: Request, db: DB
    ) -> VerificationIssued:
        user = _required_user(db, request)
        stored, sent_to = prepare_change(db, user, payload)
        code = new_verification_code()
        challenge = store_challenge(db, user, payload.purpose, stored, sent_to, code)
        try:
            delivery = deliver_verification_code(settings, sent_to, code)
        except VerificationDeliveryError as exc:
            db.rollback()
            raise HTTPException(
                status_code=502, detail="could not send the verification email"
            ) from exc
        db.commit()
        db.refresh(challenge)
        return VerificationIssued(
            purpose=payload.purpose,
            sent_to=sent_to,
            expires_at=challenge.expires_at,
            delivery=delivery,
            preview_code=code if delivery == "preview" else None,
        )

    @app.post("/api/users/me/verification/confirm", response_model=UserOut)
    def confirm_account_verification(
        payload: VerificationConfirm,
        request: Request,
        response: Response,
        db: DB,
    ) -> User:
        user = _required_user(db, request)
        challenge = take_challenge(db, user, payload.purpose, payload.code)
        apply_change(user, challenge)
        if challenge.purpose == "password":
            now = datetime.now(UTC)
            for record in db.scalars(
                select(RefreshToken).where(
                    RefreshToken.user_id == user.id,
                    RefreshToken.revoked_at.is_(None),
                )
            ):
                record.revoked_at = now
            _issue_refresh_token(db, response, user.id, settings)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409, detail="email or username already registered"
            ) from exc
        db.refresh(user)
        return user

    @app.delete("/api/users/me", status_code=204)
    def delete_current_user(
        payload: AccountDelete,
        request: Request,
        response: Response,
        db: DB,
    ) -> Response:
        user = _required_user(db, request)
        if not verify_password(payload.password, user.password_hash):
            raise HTTPException(status_code=403, detail="password confirmation failed")
        db.delete(user)
        db.commit()
        _clear_refresh_cookie(response, settings)
        response.status_code = 204
        return response

    @app.post("/api/users/me/claim-legacy-profile", response_model=ProfileOut)
    def claim_legacy_profile(payload: LegacyProfileClaim, request: Request, db: DB) -> Profile:
        user = _required_user(db, request)
        profile = _profile_or_404(db, payload.profile_id)
        if profile.user_id is not None:
            if profile.user_id == user.id:
                return profile
            raise HTTPException(status_code=409, detail="profile has already been claimed")
        if not secrets.compare_digest(
            profile.owner_key_hash, hash_token(payload.legacy_session_key)
        ):
            raise HTTPException(status_code=403, detail="legacy session does not own this profile")
        profile.user_id = user.id
        profile.owner_key_hash = f"user:{user.id}"
        db.commit()
        db.refresh(profile)
        return profile

    @app.post("/api/profiles", response_model=ProfileOut, status_code=201)
    def create_profile(payload: ProfileCreate, request: Request, db: DB) -> Profile:
        user_id = _authenticated_user_id(request)
        profile = Profile(
            name=payload.name,
            user_id=user_id,
            owner_key_hash=f"user:{user_id}" if user_id else _owner_hash(request),
        )
        db.add(profile)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="profile name already exists") from exc
        db.refresh(profile)
        return profile

    @app.put("/api/profiles/{profile_id}/onboarding-preferences", response_model=ProfileOut)
    def save_onboarding_preferences(
        profile_id: int,
        payload: OnboardingPreferencesIn,
        request: Request,
        db: DB,
    ) -> Profile:
        _owned_profile_or_404(db, profile_id, request)
        if not payload.preferred_genres:
            raise HTTPException(
                status_code=422,
                detail="select at least one genre you want to watch more of",
            )
        overlap = set(payload.preferred_genres) & set(payload.disliked_genres)
        if overlap:
            raise HTTPException(
                status_code=422,
                detail="a genre cannot be both preferred and avoided",
            )
        profile = apply_onboarding_preferences(
            db,
            profile_id,
            payload.preferred_genres,
            payload.disliked_genres,
        )
        db.commit()
        db.refresh(profile)
        return profile

    @app.get("/api/profiles", response_model=list[ProfileOut])
    def list_profiles(request: Request, db: DB) -> list[Profile]:
        if settings.read_only:
            raise HTTPException(
                status_code=403,
                detail="profile enumeration is disabled in read-only demo mode",
            )
        user_id = _authenticated_user_id(request)
        ownership = (
            Profile.user_id == user_id
            if user_id is not None
            else Profile.owner_key_hash == _owner_hash(request)
        )
        return list(db.scalars(select(Profile).where(ownership).order_by(Profile.name)))

    @app.get("/api/movies", response_model=MoviePage)
    def search_movies(
        db: DB,
        q: str | None = Query(default=None, min_length=1, max_length=100),
        genre: str | None = Query(default=None, max_length=50),
        year: int | None = Query(default=None, ge=1870, le=2200),
        language: str | None = Query(default=None, max_length=20),
        max_runtime: int | None = Query(default=None, ge=1, le=600),
        country: str | None = Query(default=None, min_length=2, max_length=2),
        provider_id: int | None = Query(default=None, gt=0),
        sort: str = Query(default="title", pattern="^(title|year|rating|popularity)$"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=48, ge=1, le=100),
    ) -> MoviePage:
        query = select(Movie)
        if q:
            query = query.where(Movie.title.ilike(f"%{_escape_like(q)}%", escape="\\"))
        if genre:
            query = query.where(movie_has_genre(genre, dialect=db.get_bind().dialect.name))
        if year:
            query = query.where(Movie.year == year)
        if language:
            query = query.where(Movie.language == language)
        if max_runtime:
            query = query.where((Movie.runtime.is_(None)) | (Movie.runtime <= max_runtime))
        if country or provider_id:
            query = query.join(
                StreamingAvailability,
                StreamingAvailability.movie_id == Movie.id,
            )
            if country:
                query = query.where(StreamingAvailability.country == country.upper())
            if provider_id:
                query = query.where(StreamingAvailability.provider_id == provider_id)
            query = query.where(StreamingAvailability.expires_at > datetime.now(UTC)).distinct()
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        order: Any = {
            "title": Movie.title.asc(),
            "year": Movie.year.desc(),
            "rating": Movie.vote_average.desc(),
            "popularity": Movie.popularity_score.desc(),
        }[sort]
        movies = list(
            db.scalars(
                query.order_by(order, Movie.id).offset((page - 1) * page_size).limit(page_size)
            ).all()
        )
        _attach_posters(db, movies, settings)
        return MoviePage(
            items=[MovieOut.model_validate(movie) for movie in movies],
            page=page,
            page_size=page_size,
            total=total,
        )

    @app.get("/api/movies/{movie_id}", response_model=MovieOut)
    def movie_detail(movie_id: int, db: DB) -> Movie:
        movie = _movie_or_404(db, movie_id)
        _attach_posters(db, [movie], settings)
        return movie

    @app.put("/api/profiles/{profile_id}/ratings/{movie_id}", response_model=RatingOut)
    def upsert_rating(
        profile_id: int, movie_id: int, payload: RatingIn, request: Request, db: DB
    ) -> Rating:
        profile = _owned_profile_or_404(db, profile_id, request)
        _movie_or_404(db, movie_id)
        rating = db.scalar(
            select(Rating).where(Rating.profile_id == profile_id, Rating.movie_id == movie_id)
        )
        if rating:
            rating.value = payload.value
        else:
            rating = Rating(profile_id=profile_id, movie_id=movie_id, value=payload.value)
            db.add(rating)
        profile.recommendation_version += 1
        _invalidate_groups(db, profile.id)
        db.commit()
        db.refresh(rating)
        return rating

    @app.put("/api/profiles/{profile_id}/interactions/{kind}/{movie_id}", status_code=204)
    def set_interaction(
        profile_id: int,
        movie_id: int,
        kind: InteractionKind,
        payload: InteractionIn,
        request: Request,
        db: DB,
    ) -> Response:
        profile = _owned_profile_or_404(db, profile_id, request)
        _movie_or_404(db, movie_id)
        existing = db.scalar(
            select(Interaction).where(
                Interaction.profile_id == profile_id,
                Interaction.movie_id == movie_id,
                Interaction.kind == kind,
            )
        )
        if payload.active and not existing:
            db.add(Interaction(profile_id=profile_id, movie_id=movie_id, kind=kind))
        elif not payload.active and existing:
            db.delete(existing)
        if kind == InteractionKind.WATCHLIST:
            state = db.scalar(
                select(UserMovieState).where(
                    UserMovieState.profile_id == profile_id,
                    UserMovieState.movie_id == movie_id,
                )
            )
            if payload.active:
                if not state:
                    state = UserMovieState(profile_id=profile_id, movie_id=movie_id)
                    db.add(state)
                state.watchlist = True
            elif state:
                state.watchlist = False
        profile.recommendation_version += 1
        _invalidate_groups(db, profile.id)
        db.commit()
        return Response(status_code=204)

    @app.get("/api/profiles/{profile_id}/interactions/{kind}", response_model=list[MovieOut])
    def list_interactions(
        profile_id: int, kind: InteractionKind, request: Request, db: DB
    ) -> list[Movie]:
        _owned_profile_or_404(db, profile_id, request)
        movies = list(
            db.scalars(
                select(Movie)
                .join(Interaction, Interaction.movie_id == Movie.id)
                .where(Interaction.profile_id == profile_id, Interaction.kind == kind)
                .order_by(Interaction.created_at.desc())
            )
        )
        _attach_posters(db, movies, settings)
        return movies

    @app.put(
        "/api/profiles/{profile_id}/preferences/{movie_id}",
        response_model=PreferenceStateOut,
    )
    def update_preference(
        profile_id: int,
        movie_id: int,
        payload: PreferenceUpdate,
        request: Request,
        db: DB,
    ) -> PreferenceStateOut:
        profile = _owned_profile_or_404(db, profile_id, request)
        _movie_or_404(db, movie_id)
        duplicate = db.scalar(
            select(PreferenceEvent).where(
                PreferenceEvent.profile_id == profile_id,
                PreferenceEvent.request_id == payload.request_id,
            )
        )
        state = db.scalar(
            select(UserMovieState).where(
                UserMovieState.profile_id == profile_id,
                UserMovieState.movie_id == movie_id,
            )
        )
        if duplicate:
            return _preference_output(profile, movie_id, state, duplicate.id)
        if payload.origin != "organic" and (
            not payload.recommendation_request_id or not payload.impression_id
        ):
            raise HTTPException(
                status_code=422,
                detail="recommendation-driven actions require request and impression IDs",
            )
        attributed_impression = None
        if payload.origin != "organic":
            attributed_impression = db.get(
                RecommendationImpression, payload.recommendation_request_id
            )
            if (
                not attributed_impression
                or payload.impression_id != attributed_impression.impression_id
                or movie_id not in attributed_impression.displayed_movie_ids
            ):
                raise HTTPException(
                    status_code=409,
                    detail="feedback does not match a displayed recommendation",
                )

        previous = _state_snapshot(state)
        if payload.action != "skip":
            if not state:
                state = UserMovieState(profile_id=profile_id, movie_id=movie_id)
                db.add(state)
            if payload.action in {"liked", "love"}:
                state.preference = "liked"
                state.watchlist = False
            elif payload.action in {"disliked", "not_interested"}:
                state.preference = "disliked"
                state.watchlist = False
            elif payload.action == "watchlist":
                state.watchlist = True
            elif payload.action == "watched":
                state.watched = True
            profile.recommendation_version += 1
            _invalidate_groups(db, profile.id)
        event = PreferenceEvent(
            profile_id=profile_id,
            movie_id=movie_id,
            request_id=payload.request_id,
            action=payload.action,
            origin=payload.origin,
            recommendation_request_id=payload.recommendation_request_id,
            impression_id=payload.impression_id,
            previous_state=previous,
            new_state=_state_snapshot(state),
        )
        db.add(event)
        db.flush()
        rebuild_taste_profile(db, profile_id)
        if attributed_impression:
            rewards = {
                "disliked": -1.0,
                "not_interested": -1.0,
                "liked": 1.0,
                "love": 1.5,
                "watchlist": 0.35,
                "watched": 0.0,
                "skip": 0.0,
            }
            db.add(
                RecommendationFeedback(
                    request_id=attributed_impression.request_id,
                    movie_id=movie_id,
                    feedback_type=payload.action,
                    reward=rewards[payload.action],
                    action_event_id=event.id,
                )
            )
        db.commit()
        db.refresh(event)
        if (
            payload.origin == "bandit_policy"
            and attributed_impression
            and attributed_impression.policy_context
            and not bool(attributed_impression.policy_context.get("shadow", True))
            and request.app.state.bandit_policy
        ):
            try:
                context = cast(dict[str, Any], attributed_impression.policy_context)
                decision = BanditDecision(
                    movie_id=int(context["movie_id"]),
                    action_index=int(context["action_index"]),
                    probability=float(context["probability"]),
                    probabilities=tuple(float(value) for value in context["probabilities"]),
                    example_lines=tuple(str(value) for value in context["example_lines"]),
                )
                request.app.state.bandit_policy.learn(
                    decision,
                    reward_for(payload.action),
                    feedback_key=f"preference-event:{event.id}",
                    ledger_path=settings.artifact_dir / "vw" / "feedback-ledger.txt",
                )
            except (KeyError, OSError, RuntimeError, TypeError, ValueError):
                logger.exception("bandit_feedback_update_failed")
        if state:
            db.refresh(state)
        return _preference_output(profile, movie_id, state, event.id)

    @app.post(
        "/api/profiles/{profile_id}/preferences/{movie_id}/undo",
        response_model=PreferenceStateOut,
    )
    def undo_preference(
        profile_id: int,
        movie_id: int,
        payload: PreferenceUndo,
        request: Request,
        db: DB,
    ) -> PreferenceStateOut:
        profile = _owned_profile_or_404(db, profile_id, request)
        latest = db.scalar(
            select(PreferenceEvent)
            .where(PreferenceEvent.profile_id == profile_id, PreferenceEvent.undone.is_(False))
            .order_by(PreferenceEvent.id.desc())
            .limit(1)
        )
        if not latest or latest.id != payload.action_id or latest.movie_id != movie_id:
            raise HTTPException(status_code=409, detail="only the latest action can be undone")
        state = db.scalar(
            select(UserMovieState).where(
                UserMovieState.profile_id == profile_id,
                UserMovieState.movie_id == movie_id,
            )
        )
        if latest.action != "skip":
            if latest.previous_state is None:
                if state:
                    db.delete(state)
                    state = None
            else:
                if not state:
                    state = UserMovieState(profile_id=profile_id, movie_id=movie_id)
                    db.add(state)
                state.watched = bool(latest.previous_state["watched"])
                state.preference = latest.previous_state.get("preference")  # type: ignore[assignment]
                state.watchlist = bool(latest.previous_state["watchlist"])
            profile.recommendation_version += 1
            _invalidate_groups(db, profile.id)
        latest.undone = True
        db.commit()
        if state:
            db.refresh(state)
        return _preference_output(profile, movie_id, state, latest.id)

    @app.get("/api/profiles/{profile_id}/recommendations", response_model=list[RecommendationOut])
    def recommendations(
        profile_id: int,
        request: Request,
        db: DB,
        limit: int = Query(default=48, ge=1, le=100),
        discovery: int = Query(default=35, ge=0, le=100),
    ) -> list[RecommendationOut]:
        profile = _owned_profile_or_404(db, profile_id, request)
        exploration = discovery / 100
        pool = _cached_recommendation_rows(
            db,
            request.app.state.recommendation_cache,
            f"personal:{profile.id}:{profile.recommendation_version}:{limit}:{discovery}",
            lambda: embedding_hybrid_recommendations(
                db,
                profile_id,
                limit,
                exploration=exploration,
                collaborative_artifact_dir=settings.artifact_dir / "implicit-als-v1",
            ),
        )
        return _log_personal_recommendations(
            db,
            profile,
            pool,
            limit,
            settings,
            _authenticated_user_id(request),
            request.app.state.bandit_policy,
        )

    @app.get("/api/recommendations/personal", response_model=list[RecommendationOut])
    def personal_recommendations(
        request: Request,
        db: DB,
        profile_id: int | None = Query(default=None, gt=0),
        limit: int = Query(default=48, ge=1, le=100),
        include_watched: bool = False,
        diversity: float = Query(default=0.18, ge=0, le=0.5),
        exploration: float = Query(default=0.15, ge=0, le=0.5),
    ) -> list[RecommendationOut]:
        user = _required_user(db, request)
        profile = (
            _owned_profile_or_404(db, profile_id, request)
            if profile_id
            else db.scalar(select(Profile).where(Profile.user_id == user.id).order_by(Profile.id))
        )
        if not profile:
            raise HTTPException(
                status_code=409, detail="create a profile before requesting recommendations"
            )
        pool_size = max(120, limit)
        pool = _cached_recommendation_rows(
            db,
            request.app.state.recommendation_cache,
            (
                f"personal:{profile.id}:{profile.recommendation_version}:{pool_size}:"
                f"{include_watched}:{diversity:.3f}:{exploration:.3f}"
            ),
            lambda: embedding_hybrid_recommendations(
                db,
                profile.id,
                pool_size,
                include_watched=include_watched,
                diversity=diversity,
                exploration=exploration,
                collaborative_artifact_dir=settings.artifact_dir / "implicit-als-v1",
            ),
        )
        return _log_personal_recommendations(
            db,
            profile,
            pool,
            limit,
            settings,
            user.id,
            request.app.state.bandit_policy,
        )

    @app.get("/api/profiles/{profile_id}/discover", response_model=list[RecommendationOut])
    def discover(
        profile_id: int,
        request: Request,
        db: DB,
        limit: int = Query(default=60, ge=1, le=100),
    ) -> list[RecommendationOut]:
        profile = _owned_profile_or_404(db, profile_id, request)
        rows = _cached_recommendation_rows(
            db,
            request.app.state.recommendation_cache,
            f"discover:{profile.id}:{profile.recommendation_version}:{limit}",
            lambda: discovery_for_profile(db, profile_id, limit),
        )
        return _recommendations_out(db, rows, settings)

    @app.get("/api/profiles/{profile_id}/wishlist", response_model=list[MovieOut])
    def profile_wishlist(profile_id: int, request: Request, db: DB) -> list[Movie]:
        _owned_profile_or_404(db, profile_id, request)
        movies = list(
            db.scalars(
                select(Movie)
                .join(UserMovieState, UserMovieState.movie_id == Movie.id)
                .where(
                    UserMovieState.profile_id == profile_id,
                    UserMovieState.watchlist.is_(True),
                )
                .order_by(UserMovieState.updated_at.desc())
            )
        )
        _attach_posters(db, movies, settings)
        return movies

    @app.delete("/api/profiles/{profile_id}/wishlist/{movie_id}", status_code=204)
    def remove_profile_wishlist(
        profile_id: int, movie_id: int, request: Request, db: DB
    ) -> Response:
        profile = _owned_profile_or_404(db, profile_id, request)
        _movie_or_404(db, movie_id)
        state = db.scalar(
            select(UserMovieState).where(
                UserMovieState.profile_id == profile_id,
                UserMovieState.movie_id == movie_id,
            )
        )
        if state:
            state.watchlist = False
        interaction = db.scalar(
            select(Interaction).where(
                Interaction.profile_id == profile_id,
                Interaction.movie_id == movie_id,
                Interaction.kind == InteractionKind.WATCHLIST,
            )
        )
        if interaction:
            db.delete(interaction)
        profile.recommendation_version += 1
        _invalidate_groups(db, profile.id)
        db.commit()
        return Response(status_code=204)

    @app.get("/api/users/me/preferences", response_model=list[PreferenceStateOut])
    def my_preferences(request: Request, db: DB) -> list[PreferenceStateOut]:
        user = _required_user(db, request)
        states = list(
            db.scalars(
                select(UserMovieState)
                .join(Profile, Profile.id == UserMovieState.profile_id)
                .where(Profile.user_id == user.id)
                .order_by(UserMovieState.updated_at.desc())
            )
        )
        versions = {
            profile.id: profile.recommendation_version
            for profile in db.scalars(select(Profile).where(Profile.user_id == user.id))
        }
        return [
            PreferenceStateOut(
                profile_id=state.profile_id,
                movie_id=state.movie_id,
                watched=state.watched,
                preference=state.preference,  # type: ignore[arg-type]
                watchlist=state.watchlist,
                updated_at=state.updated_at,
                recommendation_version=versions[state.profile_id],
            )
            for state in states
        ]

    @app.get("/api/users/me/wishlist", response_model=list[MovieOut])
    def my_wishlist(request: Request, db: DB) -> list[Movie]:
        user = _required_user(db, request)
        movies = list(
            db.scalars(
                select(Movie)
                .join(UserMovieState, UserMovieState.movie_id == Movie.id)
                .join(Profile, Profile.id == UserMovieState.profile_id)
                .where(Profile.user_id == user.id, UserMovieState.watchlist.is_(True))
                .distinct()
                .order_by(Movie.title)
            )
        )
        _attach_posters(db, movies, settings)
        return movies

    @app.post("/api/users/me/recommendations/reset", status_code=204)
    def reset_my_recommendations(request: Request, db: DB) -> Response:
        user = _required_user(db, request)
        profiles = list(db.scalars(select(Profile).where(Profile.user_id == user.id)))
        profile_ids = [profile.id for profile in profiles]
        for state in db.scalars(
            select(UserMovieState).where(UserMovieState.profile_id.in_(profile_ids))
        ):
            state.preference = None
        for event in db.scalars(
            select(PreferenceEvent).where(
                PreferenceEvent.profile_id.in_(profile_ids),
                PreferenceEvent.action.in_(["liked", "love", "disliked", "not_interested"]),
                PreferenceEvent.undone.is_(False),
            )
        ):
            event.undone = True
        for profile in profiles:
            profile.recommendation_version += 1
            _invalidate_groups(db, profile.id)
        db.commit()
        return Response(status_code=204)

    @app.get("/api/movies/{movie_id}/similar", response_model=list[RecommendationOut])
    def similar(
        movie_id: int, db: DB, limit: int = Query(default=20, ge=1, le=100)
    ) -> list[RecommendationOut]:
        movie = _movie_or_404(db, movie_id)
        return _recommendations_out(db, similar_movies(db, movie, limit), settings)

    @app.post("/api/recommendations/group", response_model=list[RecommendationOut])
    def legacy_group_recommendations(
        payload: GroupRequest, request: Request, db: DB
    ) -> list[RecommendationOut]:
        for profile_id in payload.profile_ids:
            _owned_profile_or_404(db, profile_id, request)
        return _recommendations_out(
            db,
            group_recommendations(
                db,
                payload.profile_ids,
                payload.limit,
                mean_weight=0.75,
                include_watched=payload.include_watched,
            ),
            settings,
        )

    @app.post("/api/groups", response_model=GroupOut, status_code=201)
    def create_group(payload: GroupCreate, request: Request, db: DB) -> GroupOut:
        owner = _owned_profile_or_404(db, payload.profile_id, request)
        group = Group(name=payload.name, owner_profile_id=owner.id)
        db.add(group)
        db.flush()
        db.add(GroupMember(group_id=group.id, profile_id=owner.id, role="owner"))
        db.commit()
        return _group_output(db, group)

    @app.get("/api/groups", response_model=list[GroupOut])
    def list_groups(request: Request, db: DB) -> list[GroupOut]:
        user_id = _authenticated_user_id(request)
        ownership = (
            Profile.user_id == user_id
            if user_id is not None
            else Profile.owner_key_hash == _owner_hash(request)
        )
        groups = db.scalars(
            select(Group)
            .join(GroupMember, GroupMember.group_id == Group.id)
            .join(Profile, Profile.id == GroupMember.profile_id)
            .where(ownership)
            .distinct()
            .order_by(Group.created_at.desc())
        ).all()
        return [_group_output(db, group) for group in groups]

    @app.post("/api/groups/{group_id}/invites", response_model=InviteOut, status_code=201)
    def create_invite(
        group_id: int,
        payload: InviteCreate,
        request: Request,
        db: DB,
    ) -> InviteOut:
        profile = _owned_profile_or_404(db, payload.profile_id, request)
        group = _group_member_or_404(db, group_id, profile.id)
        token = secrets.token_urlsafe(32)
        invite = GroupInvite(
            group_id=group.id,
            token_hash=_hash_token(token),
            invite_token=token,
            created_by_profile_id=profile.id,
            expires_at=datetime.now(UTC) + timedelta(hours=payload.expires_in_hours),
        )
        db.add(invite)
        db.flush()
        room = _ensure_invite_room(db, group, invite)
        db.commit()
        db.refresh(invite)
        return InviteOut(
            id=invite.id,
            token=token,
            invite_url=_invite_public_url(token, settings),
            expires_at=invite.expires_at,
            room_id=room.id,
            room_name=room.name,
        )

    @app.delete("/api/groups/{group_id}/invites/{invite_id}", status_code=204)
    def revoke_invite(
        group_id: int,
        invite_id: int,
        profile_id: int,
        request: Request,
        db: DB,
    ) -> Response:
        profile = _owned_profile_or_404(db, profile_id, request)
        group = _group_member_or_404(db, group_id, profile.id)
        if group.owner_profile_id != profile.id:
            raise HTTPException(status_code=403, detail="only the group owner can revoke invites")
        invite = db.get(GroupInvite, invite_id)
        if not invite or invite.group_id != group_id:
            raise HTTPException(status_code=404, detail="invite not found")
        invite.revoked_at = datetime.now(UTC)
        db.commit()
        return Response(status_code=204)

    @app.post("/api/group-invites/{token}/join", response_model=GroupJoinOut)
    def join_group(
        token: str,
        payload: InviteJoin,
        request: Request,
        db: DB,
    ) -> GroupJoinOut:
        profile = _owned_profile_or_404(db, payload.profile_id, request)
        invite = db.scalar(select(GroupInvite).where(GroupInvite.token_hash == _hash_token(token)))
        if not invite:
            raise HTTPException(status_code=404, detail="invitation is invalid")
        if invite.revoked_at:
            raise HTTPException(status_code=410, detail="invitation has been revoked")
        expires_at = invite.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=410, detail="invitation has expired")
        group = db.get(Group, invite.group_id)
        if not group:
            raise HTTPException(status_code=404, detail="group not found")
        existing = db.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group.id,
                GroupMember.profile_id == profile.id,
            )
        )
        if not existing:
            db.add(GroupMember(group_id=group.id, profile_id=profile.id, role="member"))
            group.recommendation_version += 1
        room = _ensure_invite_room(db, group, invite)
        _add_room_member(db, room, profile.id)
        db.commit()
        group_out = _group_output(db, group)
        return GroupJoinOut(
            **group_out.model_dump(),
            joined_room_id=room.id,
            joined_room_name=room.name,
        )

    @app.delete("/api/groups/{group_id}/members/{member_profile_id}", status_code=204)
    def remove_group_member(
        group_id: int,
        member_profile_id: int,
        requester_profile_id: int,
        request: Request,
        db: DB,
    ) -> Response:
        requester = _owned_profile_or_404(db, requester_profile_id, request)
        group = _group_member_or_404(db, group_id, requester.id)
        if requester.id != member_profile_id and group.owner_profile_id != requester.id:
            raise HTTPException(status_code=403, detail="only owners can remove other members")
        if member_profile_id == group.owner_profile_id:
            raise HTTPException(
                status_code=409,
                detail="the owner cannot leave without deleting the group",
            )
        membership = db.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.profile_id == member_profile_id,
            )
        )
        if not membership:
            raise HTTPException(status_code=404, detail="membership not found")
        db.delete(membership)
        group.recommendation_version += 1
        for active_session in db.scalars(
            select(GroupSession).where(
                GroupSession.group_id == group_id,
                GroupSession.status == "active",
            )
        ):
            if member_profile_id in active_session.participant_profile_ids:
                active_session.participant_profile_ids = [
                    value
                    for value in active_session.participant_profile_ids
                    if value != member_profile_id
                ]
                active_session.participant_set_version += 1
        _remove_profile_from_group_rooms(db, group_id, member_profile_id)
        db.commit()
        return Response(status_code=204)

    @app.post(
        "/api/groups/{group_id}/recommendations",
        response_model=list[RecommendationOut],
    )
    def ranked_group_recommendations(
        group_id: int,
        payload: GroupRankingRequest,
        request: Request,
        db: DB,
    ) -> list[RecommendationOut]:
        requester = _owned_profile_or_404(db, payload.requester_profile_id, request)
        group = _group_member_or_404(db, group_id, requester.id)
        profile_ids = list(
            db.scalars(select(GroupMember.profile_id).where(GroupMember.group_id == group_id))
        )
        mean_weight = (
            payload.mean_weight
            if payload.strategy == "custom" and payload.mean_weight is not None
            else 0.75
            if payload.strategy == "balanced"
            else 0.25
        )
        cache_key = (
            f"group:{group.id}:{group.recommendation_version}:{sorted(profile_ids)}:"
            f"{payload.limit}:{mean_weight}:{payload.include_watched}:"
            f"{payload.genres}:{payload.language}:{payload.max_runtime}"
        )
        rows = _cached_recommendation_rows(
            db,
            request.app.state.recommendation_cache,
            cache_key,
            lambda: group_recommendations(
                db,
                profile_ids,
                payload.limit,
                mean_weight=mean_weight,
                include_watched=payload.include_watched,
                genres=payload.genres,
                language=payload.language,
                max_runtime=payload.max_runtime,
            ),
        )
        return _recommendations_out(db, rows, settings)

    @app.get("/api/groups/{group_id}/rooms", response_model=list[GroupRoomOut])
    def list_group_rooms(
        group_id: int,
        request: Request,
        db: DB,
        profile_id: int = Query(gt=0),
    ) -> list[GroupRoomOut]:
        profile = _owned_profile_or_404(db, profile_id, request)
        group = _group_member_or_404(db, group_id, profile.id)
        return [_room_output(db, room) for room in _rooms_visible_to(db, group, profile.id)]

    @app.get(
        "/api/groups/{group_id}/rooms/{room_id}/invite-link",
        response_model=RoomInviteLinkOut,
    )
    def room_invite_link(
        group_id: int,
        room_id: int,
        request: Request,
        db: DB,
        profile_id: int = Query(gt=0),
    ) -> RoomInviteLinkOut:
        profile = _owned_profile_or_404(db, profile_id, request)
        group = _group_member_or_404(db, group_id, profile.id)
        room = _visible_room_or_404(db, group, room_id, profile.id)
        _room_host_or_403(room, profile.id)
        invite = db.get(GroupInvite, room.invite_id)
        if not invite:
            raise HTTPException(status_code=404, detail="invitation not found")
        token = _ensure_invite_token(db, invite)
        db.commit()
        db.refresh(invite)
        return RoomInviteLinkOut(
            invite_url=_invite_public_url(token, settings),
            expires_at=invite.expires_at,
            room_id=room.id,
            room_name=room.name,
        )

    @app.delete("/api/groups/{group_id}/rooms/{room_id}", status_code=204)
    def delete_room(
        group_id: int,
        room_id: int,
        request: Request,
        db: DB,
        profile_id: int = Query(gt=0),
    ) -> Response:
        profile = _owned_profile_or_404(db, profile_id, request)
        group = _group_member_or_404(db, group_id, profile.id)
        room = _room_in_group_or_404(db, group_id, room_id)
        _room_delete_authorizer(group, room, profile.id)
        _cancel_active_room_sessions(db, room.id)
        invite_id = room.invite_id
        db.delete(room)
        invite = db.get(GroupInvite, invite_id)
        if invite:
            db.delete(invite)
        db.commit()
        return Response(status_code=204)

    @app.delete(
        "/api/groups/{group_id}/rooms/{room_id}/members/{member_profile_id}",
        status_code=204,
    )
    def remove_room_member(
        group_id: int,
        room_id: int,
        member_profile_id: int,
        request: Request,
        db: DB,
        requester_profile_id: int = Query(gt=0),
    ) -> Response:
        requester = _owned_profile_or_404(db, requester_profile_id, request)
        group = _group_member_or_404(db, group_id, requester.id)
        room = _visible_room_or_404(db, group, room_id, requester.id)
        if member_profile_id == room.host_profile_id:
            raise HTTPException(status_code=409, detail="the host cannot be removed from the room")
        if requester.id != room.host_profile_id and requester.id != member_profile_id:
            raise HTTPException(
                status_code=403,
                detail="only the room host can remove other members",
            )
        membership = db.scalar(
            select(GroupRoomMember).where(
                GroupRoomMember.room_id == room.id,
                GroupRoomMember.profile_id == member_profile_id,
            )
        )
        if not membership:
            raise HTTPException(status_code=404, detail="room membership not found")
        db.delete(membership)
        _remove_profile_from_room_sessions(db, room.id, member_profile_id)
        db.commit()
        return Response(status_code=204)

    @app.post(
        "/api/groups/{group_id}/rooms/{room_id}/recommendations",
        response_model=list[RecommendationOut],
    )
    def ranked_room_recommendations(
        group_id: int,
        room_id: int,
        payload: GroupRankingRequest,
        request: Request,
        db: DB,
    ) -> list[RecommendationOut]:
        requester = _owned_profile_or_404(db, payload.requester_profile_id, request)
        group = _group_member_or_404(db, group_id, requester.id)
        room = _visible_room_or_404(db, group, room_id, requester.id)
        profile_ids = list(
            db.scalars(
                select(GroupRoomMember.profile_id).where(GroupRoomMember.room_id == room.id)
            )
        )
        mean_weight = (
            payload.mean_weight
            if payload.strategy == "custom" and payload.mean_weight is not None
            else 0.75
            if payload.strategy == "balanced"
            else 0.25
        )
        cache_key = (
            f"room:{group.id}:{group.recommendation_version}:{room.id}:"
            f"{sorted(profile_ids)}:{payload.limit}:{mean_weight}:{payload.include_watched}:"
            f"{payload.genres}:{payload.language}:{payload.max_runtime}"
        )
        rows = _cached_recommendation_rows(
            db,
            request.app.state.recommendation_cache,
            cache_key,
            lambda: group_recommendations(
                db,
                profile_ids,
                payload.limit,
                mean_weight=mean_weight,
                include_watched=payload.include_watched,
                genres=payload.genres,
                language=payload.language,
                max_runtime=payload.max_runtime,
            ),
        )
        return _recommendations_out(db, rows, settings)

    @app.post(
        "/api/groups/{group_id}/sessions",
        response_model=GroupSessionOut,
        status_code=201,
    )
    def create_group_session(
        group_id: int,
        payload: GroupSessionCreate,
        request: Request,
        db: DB,
    ) -> GroupSession:
        requester = _owned_profile_or_404(db, payload.requester_profile_id, request)
        _group_member_or_404(db, group_id, requester.id)
        memberships = list(
            db.scalars(
                select(GroupMember).where(
                    GroupMember.group_id == group_id,
                    GroupMember.consent_to_use_personal_preferences.is_(True),
                )
            )
        )
        allowed = {membership.profile_id for membership in memberships}
        if payload.room_id is not None:
            room = db.get(GroupRoom, payload.room_id)
            if not room or room.group_id != group_id:
                raise HTTPException(status_code=404, detail="room not found")
            if room.host_profile_id != requester.id:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "only the room host can choose a recommendation mode "
                        "and start movie night"
                    ),
                )
            room_member_ids = set(
                db.scalars(
                    select(GroupRoomMember.profile_id).where(
                        GroupRoomMember.room_id == room.id
                    )
                )
            )
            allowed &= room_member_ids
        participants = payload.participant_profile_ids or sorted(allowed)
        if requester.id not in participants or not set(participants).issubset(allowed):
            raise HTTPException(
                status_code=422,
                detail="participants must be consenting group members and include requester",
            )
        filters: dict[str, object] = {
            "country": payload.country.upper() if payload.country else None,
            "provider_ids": payload.provider_ids,
            "max_runtime": payload.max_runtime,
            "languages": payload.languages,
            "genres": payload.genres,
            "age_rating": payload.age_rating,
            "exclusions": payload.exclusions,
        }
        group_session = GroupSession(
            id=str(uuid.uuid4()),
            group_id=group_id,
            room_id=payload.room_id,
            filters=filters,
            consensus_policy=payload.consensus_policy,
            ranking_algorithm=payload.ranking_algorithm,
            participant_profile_ids=list(dict.fromkeys(participants)),
        )
        db.add(group_session)
        db.commit()
        db.refresh(group_session)
        return group_session

    @app.get("/api/groups/{group_id}/sessions", response_model=list[GroupSessionOut])
    def list_group_sessions(
        group_id: int,
        request: Request,
        db: DB,
        profile_id: int = Query(gt=0),
    ) -> list[GroupSession]:
        profile = _owned_profile_or_404(db, profile_id, request)
        group = _group_member_or_404(db, group_id, profile.id)
        sessions = list(
            db.scalars(
                select(GroupSession)
                .where(GroupSession.group_id == group_id)
                .order_by(GroupSession.created_at.desc())
            )
        )
        if group.owner_profile_id == profile.id:
            return sessions
        visible_room_ids = {room.id for room in _rooms_visible_to(db, group, profile.id)}
        return [
            session
            for session in sessions
            if session.room_id in visible_room_ids
            or (
                session.room_id is None
                and profile.id in session.participant_profile_ids
            )
        ]

    @app.patch("/api/sessions/{session_id}", response_model=GroupSessionOut)
    def update_group_session_ranking(
        session_id: str,
        payload: GroupSessionRankingUpdate,
        request: Request,
        db: DB,
    ) -> GroupSession:
        requester = _owned_profile_or_404(db, payload.requester_profile_id, request)
        group_session = db.get(GroupSession, session_id)
        if not group_session:
            raise HTTPException(status_code=404, detail="group session not found")
        _group_member_or_404(db, group_session.group_id, requester.id)
        _session_ranking_editor_or_403(db, group_session, requester.id)
        if group_session.ranking_algorithm == payload.ranking_algorithm:
            return group_session
        group_session.ranking_algorithm = payload.ranking_algorithm
        db.commit()
        db.refresh(group_session)
        return group_session

    @app.post("/api/sessions/{session_id}/votes", response_model=GroupVoteOut)
    def cast_group_vote(
        session_id: str,
        payload: GroupVoteIn,
        request: Request,
        background_tasks: BackgroundTasks,
        db: DB,
    ) -> GroupVote:
        profile = _owned_profile_or_404(db, payload.profile_id, request)
        group_session = _active_session_for_participant(db, session_id, profile.id)
        _movie_or_404(db, payload.movie_id)
        duplicate = db.scalar(select(GroupVote).where(GroupVote.request_id == payload.request_id))
        if duplicate:
            return duplicate
        vote = db.scalar(
            select(GroupVote).where(
                GroupVote.session_id == session_id,
                GroupVote.profile_id == profile.id,
                GroupVote.movie_id == payload.movie_id,
            )
        )
        if vote:
            vote.action = payload.action
            vote.request_id = payload.request_id
            vote.created_at = datetime.now(UTC)
        else:
            vote = GroupVote(
                session_id=group_session.id,
                profile_id=profile.id,
                movie_id=payload.movie_id,
                action=payload.action,
                request_id=payload.request_id,
            )
            db.add(vote)
        db.commit()
        db.refresh(vote)
        background_tasks.add_task(
            _broadcast_session,
            session_id,
            {
                "type": "vote_updated",
                "movie_id": vote.movie_id,
                "participant_set_version": group_session.participant_set_version,
            },
        )
        return vote

    @app.get(
        "/api/sessions/{session_id}/recommendations",
        response_model=list[RecommendationOut],
    )
    def session_recommendations(
        session_id: str,
        request: Request,
        db: DB,
        profile_id: int = Query(gt=0),
        limit: int = Query(default=40, ge=1, le=100),
    ) -> list[RecommendationOut]:
        profile = _owned_profile_or_404(db, profile_id, request)
        group_session = _active_session_for_participant(db, session_id, profile.id)
        vote_fingerprint = sorted(
            (vote.profile_id, vote.movie_id, vote.action)
            for vote in db.scalars(select(GroupVote).where(GroupVote.session_id == session_id))
        )
        ranked_rows = _cached_recommendation_rows(
            db,
            request.app.state.recommendation_cache,
            (
                f"session:{session_id}:{group_session.ranking_algorithm}:"
                f"{group_session.participant_set_version}:{vote_fingerprint}:{max(80, limit)}"
            ),
            lambda: [
                (row.movie, row.score, row.reason)
                for row in rank_group_session(db, group_session, limit=max(80, limit))
            ],
        )
        voted_by_requester = {
            vote.movie_id
            for vote in db.scalars(
                select(GroupVote).where(
                    GroupVote.session_id == session_id,
                    GroupVote.profile_id == profile.id,
                )
            )
        }
        pending_rows = [row for row in ranked_rows if row[0].id not in voted_by_requester]
        completed_rows = [row for row in ranked_rows if row[0].id in voted_by_requester]
        ordered_rows = pending_rows + completed_rows
        request_id = str(uuid.uuid4())
        impression_id = str(uuid.uuid4())
        displayed = ordered_rows[:limit]
        db.add(
            RecommendationImpression(
                request_id=request_id,
                impression_id=impression_id,
                user_id=_authenticated_user_id(request),
                session_id=session_id,
                context="group_session",
                candidate_movie_ids=[movie.id for movie, _score, _reason in ranked_rows],
                displayed_movie_ids=[movie.id for movie, _score, _reason in displayed],
                displayed_positions=list(range(len(displayed))),
                model_version=f"group-{group_session.ranking_algorithm}-v1",
                feature_version="group-member-scores-v1",
                policy_version="none",
                experiment_version="control",
                selection_probabilities=[None] * len(displayed),
            )
        )
        db.commit()
        return _recommendations_out(
            db,
            displayed,
            settings,
            request_id=request_id,
            impression_id=impression_id,
            model_version=f"group-{group_session.ranking_algorithm}-v1",
            feature_version="group-member-scores-v1",
        )

    @app.post("/api/sessions/{session_id}/finalize", response_model=GroupSessionOut)
    def finalize_group_session(
        session_id: str,
        payload: GroupFinalizeIn,
        request: Request,
        db: DB,
    ) -> GroupSession:
        profile = _owned_profile_or_404(db, payload.requester_profile_id, request)
        group_session = _active_session_for_participant(db, session_id, profile.id)
        _movie_or_404(db, payload.movie_id)
        if not consensus_reached(db, group_session, payload.movie_id):
            raise HTTPException(status_code=409, detail="configured consensus has not been reached")
        group_session.selected_movie_id = payload.movie_id
        group_session.status = "finalized"
        group_session.finalized_at = datetime.now(UTC)
        db.commit()
        db.refresh(group_session)
        return group_session

    @app.post("/api/sessions/{session_id}/satisfaction", status_code=204)
    def record_group_satisfaction(
        session_id: str,
        payload: GroupSatisfactionIn,
        request: Request,
        db: DB,
    ) -> Response:
        profile = _owned_profile_or_404(db, payload.profile_id, request)
        group_session = db.get(GroupSession, session_id)
        if (
            not group_session
            or profile.id not in group_session.participant_profile_ids
            or group_session.status != "finalized"
        ):
            raise HTTPException(status_code=409, detail="session is not finalized for participant")
        existing = db.scalar(
            select(GroupSatisfaction).where(
                GroupSatisfaction.session_id == session_id,
                GroupSatisfaction.profile_id == profile.id,
            )
        )
        if existing:
            existing.satisfaction = payload.satisfaction
            existing.accepted = payload.accepted
        else:
            db.add(
                GroupSatisfaction(
                    session_id=session_id,
                    profile_id=profile.id,
                    satisfaction=payload.satisfaction,
                    accepted=payload.accepted,
                )
            )
        db.commit()
        return Response(status_code=204)

    @app.websocket("/api/sessions/{session_id}/ws")
    async def session_updates(
        websocket: WebSocket,
        session_id: str,
        token: str,
        profile_id: int,
    ) -> None:
        try:
            user_id = decode_access_token(token, settings)
        except ValueError:
            await websocket.close(code=4401)
            return
        with factory() as db:
            profile = db.get(Profile, profile_id)
            group_session = db.get(GroupSession, session_id)
            allowed = bool(
                profile
                and profile.user_id == user_id
                and group_session
                and profile_id in group_session.participant_profile_ids
            )
        if not allowed:
            await websocket.close(code=4403)
            return
        await websocket.accept()
        sockets = _session_sockets.setdefault(session_id, set())
        sockets.add(websocket)
        await websocket.send_json({"type": "connected", "session_id": session_id})
        try:
            while True:
                await websocket.receive_text()
                await websocket.send_json({"type": "heartbeat"})
        except WebSocketDisconnect:
            sockets.discard(websocket)
            if not sockets:
                _session_sockets.pop(session_id, None)

    @app.get("/api/models", response_model=list[ModelRunOut])
    def model_runs(db: DB) -> list[ModelRunOut]:
        stored = list(db.scalars(select(ModelRun).order_by(ModelRun.created_at.desc())))
        if stored:
            return [ModelRunOut.model_validate(run) for run in stored]
        return _evaluation_runs(settings.artifact_dir)

    @app.get("/api/settings", response_model=SystemMetadataOut)
    def system_metadata(db: DB) -> SystemMetadataOut:
        latest = db.scalar(select(ModelRun).order_by(ModelRun.created_at.desc()).limit(1))
        generated = _evaluation_runs(settings.artifact_dir)
        active = max(generated, key=_ndcg) if generated else None
        active_model = (
            latest.model_name if latest else active.model_name if active else "bayesian-popularity"
        )
        return SystemMetadataOut(
            active_model=active_model,
            dataset="MovieLens latest-small",
            movie_count=db.scalar(select(func.count()).select_from(Movie)) or 0,
            rating_count=db.scalar(select(func.count()).select_from(Rating)) or 0,
            last_trained=latest.created_at if latest else active.created_at if active else None,
            read_only=settings.read_only,
            tmdb_available=bool(settings.tmdb_token),
        )

    @app.get("/api/health/models")
    def model_health(request: Request, db: DB) -> dict[str, object]:
        _required_user(db, request)
        movie_count = db.scalar(select(func.count()).select_from(Movie)) or 0
        embedding_count = db.scalar(select(func.count()).select_from(MovieEmbedding)) or 0
        impression_count = (
            db.scalar(select(func.count()).select_from(RecommendationImpression)) or 0
        )
        fallback_count = (
            db.scalar(
                select(func.count())
                .select_from(RecommendationImpression)
                .where(RecommendationImpression.model_version.contains("fallback"))
            )
            or 0
        )
        return {
            "embedding_coverage": embedding_count / max(movie_count, 1),
            "impression_count": impression_count,
            "fallback_rate": fallback_count / max(impression_count, 1),
            "bandit_available": bool(request.app.state.bandit_policy),
            "collaborative_available": (
                settings.artifact_dir / "implicit-als-v1" / "manifest.json"
            ).exists(),
        }


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _profile_or_404(db: Session, profile_id: int) -> Profile:
    profile = db.get(Profile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="profile not found")
    return profile


def _owner_hash(request: Request) -> str:
    token = request.headers.get("x-cinematch-session", "")
    if len(token) < 16:
        raise HTTPException(status_code=401, detail="a valid session key is required")
    return _hash_token(token)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _authenticated_user_id(request: Request) -> int | None:
    authorization = request.headers.get("authorization", "")
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="invalid authorization header")
    try:
        return decode_access_token(token, request.app.state.settings)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _required_user(db: Session, request: Request) -> User:
    user_id = _authenticated_user_id(request)
    user = db.get(User, user_id) if user_id else None
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    same_site: Literal["lax", "none"] = "none" if settings.secure_cookies else "lax"
    response.delete_cookie(
        "cinematch_refresh",
        path="/api/auth",
        httponly=True,
        secure=settings.secure_cookies,
        samesite=same_site,
    )


def _issue_refresh_token(db: Session, response: Response, user_id: int, settings: Settings) -> None:
    raw, token_hash = new_refresh_token()
    expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_days)
    same_site: Literal["lax", "none"] = "none" if settings.secure_cookies else "lax"
    db.add(RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at))
    response.set_cookie(
        "cinematch_refresh",
        raw,
        max_age=settings.refresh_token_days * 86_400,
        expires=expires_at,
        path="/api/auth",
        httponly=True,
        secure=settings.secure_cookies,
        samesite=same_site,
    )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _owned_profile_or_404(db: Session, profile_id: int, request: Request) -> Profile:
    profile = _profile_or_404(db, profile_id)
    user_id = _authenticated_user_id(request)
    if user_id is not None:
        if profile.user_id != user_id:
            raise HTTPException(status_code=403, detail="profile is not owned by this user")
        return profile
    if profile.user_id is not None or not secrets.compare_digest(
        profile.owner_key_hash, _owner_hash(request)
    ):
        raise HTTPException(status_code=403, detail="profile is not owned by this session")
    return profile


def _state_snapshot(state: UserMovieState | None) -> dict[str, object] | None:
    if not state:
        return None
    return {
        "watched": state.watched,
        "preference": state.preference,
        "watchlist": state.watchlist,
    }


def _preference_output(
    profile: Profile,
    movie_id: int,
    state: UserMovieState | None,
    action_id: int,
) -> PreferenceStateOut:
    return PreferenceStateOut(
        profile_id=profile.id,
        movie_id=movie_id,
        watched=state.watched if state else False,
        preference=state.preference if state else None,  # type: ignore[arg-type]
        watchlist=state.watchlist if state else False,
        updated_at=state.updated_at if state else datetime.now(UTC),
        action_id=action_id,
        recommendation_version=profile.recommendation_version,
    )


def _group_member_or_404(db: Session, group_id: int, profile_id: int) -> Group:
    group = db.get(Group, group_id)
    membership = db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.profile_id == profile_id,
        )
    )
    if not group or not membership:
        raise HTTPException(status_code=404, detail="group not found")
    return group


def _active_session_for_participant(db: Session, session_id: str, profile_id: int) -> GroupSession:
    group_session = db.get(GroupSession, session_id)
    if not group_session:
        raise HTTPException(status_code=404, detail="group session not found")
    if profile_id not in group_session.participant_profile_ids:
        raise HTTPException(status_code=403, detail="profile is not a session participant")
    if group_session.status != "active":
        raise HTTPException(status_code=409, detail="group session is not active")
    _group_member_or_404(db, group_session.group_id, profile_id)
    return group_session


def _session_ranking_editor_or_403(
    db: Session, group_session: GroupSession, profile_id: int
) -> None:
    if group_session.status != "active":
        raise HTTPException(status_code=409, detail="group session is not active")
    if group_session.room_id is not None:
        room = db.get(GroupRoom, group_session.room_id)
        if not room or room.host_profile_id != profile_id:
            raise HTTPException(
                status_code=403,
                detail="only the room host can change the recommendation mode",
            )
        return
    if profile_id not in group_session.participant_profile_ids:
        raise HTTPException(status_code=403, detail="profile is not a session participant")


def _invite_public_url(token: str, settings: Settings) -> str:
    return f"{settings.public_app_url.rstrip('/')}/#/invite/{token}"


def _room_host_or_403(room: GroupRoom, profile_id: int) -> None:
    if room.host_profile_id != profile_id:
        raise HTTPException(status_code=403, detail="only the room host can do that")


def _ensure_invite_token(db: Session, invite: GroupInvite) -> str:
    now = datetime.now(UTC)
    expires_at = invite.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if invite.invite_token and not invite.revoked_at and expires_at > now:
        return invite.invite_token
    token = secrets.token_urlsafe(32)
    invite.token_hash = _hash_token(token)
    invite.invite_token = token
    invite.revoked_at = None
    if expires_at <= now:
        invite.expires_at = now + timedelta(hours=48)
    return token


def _cancel_active_room_sessions(db: Session, room_id: int) -> None:
    for active in db.scalars(
        select(GroupSession).where(
            GroupSession.room_id == room_id,
            GroupSession.status == "active",
        )
    ):
        active.status = "cancelled"


def _remove_profile_from_room_sessions(
    db: Session, room_id: int, profile_id: int
) -> None:
    for active in db.scalars(
        select(GroupSession).where(
            GroupSession.room_id == room_id,
            GroupSession.status == "active",
        )
    ):
        if profile_id not in active.participant_profile_ids:
            continue
        active.participant_profile_ids = [
            value for value in active.participant_profile_ids if value != profile_id
        ]
        active.participant_set_version += 1


def _next_room_name(db: Session, group_id: int, profile_name: str) -> str:
    base = f"{profile_name}'s room"
    taken = set(db.scalars(select(GroupRoom.name).where(GroupRoom.group_id == group_id)))
    if base not in taken:
        return base
    number = 2
    while f"{base} {number}" in taken:
        number += 1
    return f"{base} {number}"


def _ensure_invite_room(db: Session, group: Group, invite: GroupInvite) -> GroupRoom:
    room = db.scalar(select(GroupRoom).where(GroupRoom.invite_id == invite.id))
    if room:
        return room
    host_id = invite.created_by_profile_id or group.owner_profile_id
    host = db.get(Profile, host_id)
    room = GroupRoom(
        group_id=group.id,
        invite_id=invite.id,
        host_profile_id=host_id,
        name=_next_room_name(db, group.id, host.name if host else "Host"),
    )
    db.add(room)
    db.flush()
    db.add(GroupRoomMember(room_id=room.id, profile_id=host_id))
    db.flush()
    return room


def _add_room_member(db: Session, room: GroupRoom, profile_id: int) -> None:
    existing = db.scalar(
        select(GroupRoomMember).where(
            GroupRoomMember.room_id == room.id,
            GroupRoomMember.profile_id == profile_id,
        )
    )
    if not existing:
        db.add(GroupRoomMember(room_id=room.id, profile_id=profile_id))


def _rooms_visible_to(db: Session, group: Group, profile_id: int) -> list[GroupRoom]:
    rooms = list(
        db.scalars(
            select(GroupRoom)
            .where(GroupRoom.group_id == group.id)
            .order_by(GroupRoom.created_at, GroupRoom.id)
        )
    )
    if group.owner_profile_id == profile_id:
        return rooms
    joined = set(
        db.scalars(
            select(GroupRoomMember.room_id).where(GroupRoomMember.profile_id == profile_id)
        )
    )
    return [room for room in rooms if room.id in joined]


def _visible_room_or_404(db: Session, group: Group, room_id: int, profile_id: int) -> GroupRoom:
    visible = next(
        (room for room in _rooms_visible_to(db, group, profile_id) if room.id == room_id),
        None,
    )
    if not visible:
        raise HTTPException(status_code=404, detail="room not found")
    return visible


def _room_in_group_or_404(db: Session, group_id: int, room_id: int) -> GroupRoom:
    room = db.scalar(
        select(GroupRoom).where(
            GroupRoom.id == room_id,
            GroupRoom.group_id == group_id,
        )
    )
    if not room:
        raise HTTPException(status_code=404, detail="room not found")
    return room


def _room_delete_authorizer(group: Group, room: GroupRoom, profile_id: int) -> None:
    if room.host_profile_id == profile_id or group.owner_profile_id == profile_id:
        return
    raise HTTPException(
        status_code=403,
        detail="only the room host or group owner can delete this room",
    )


def _room_output(db: Session, room: GroupRoom) -> GroupRoomOut:
    host = db.get(Profile, room.host_profile_id)
    rows = db.execute(
        select(GroupRoomMember, Profile)
        .join(Profile, Profile.id == GroupRoomMember.profile_id)
        .where(GroupRoomMember.room_id == room.id)
        .order_by(GroupRoomMember.joined_at, GroupRoomMember.id)
    ).all()
    return GroupRoomOut(
        id=room.id,
        group_id=room.group_id,
        invite_id=room.invite_id,
        host_profile_id=room.host_profile_id,
        host_name=host.name if host else "Host",
        name=room.name,
        members=[
            GroupRoomMemberOut(
                profile_id=profile.id,
                name=profile.name,
                host=profile.id == room.host_profile_id,
            )
            for _, profile in rows
        ],
    )


def _remove_profile_from_group_rooms(db: Session, group_id: int, profile_id: int) -> None:
    hosted = list(
        db.scalars(
            select(GroupRoom).where(
                GroupRoom.group_id == group_id,
                GroupRoom.host_profile_id == profile_id,
            )
        )
    )
    hosted_ids = [room.id for room in hosted]
    if hosted_ids:
        for active in db.scalars(
            select(GroupSession).where(
                GroupSession.room_id.in_(hosted_ids),
                GroupSession.status == "active",
            )
        ):
            active.status = "cancelled"
        for room in hosted:
            db.delete(room)
    room_ids = list(
        db.scalars(select(GroupRoom.id).where(GroupRoom.group_id == group_id))
    )
    if not room_ids:
        return
    for membership in db.scalars(
        select(GroupRoomMember).where(
            GroupRoomMember.profile_id == profile_id,
            GroupRoomMember.room_id.in_(room_ids),
        )
    ):
        db.delete(membership)


def _group_output(db: Session, group: Group) -> GroupOut:
    members = db.execute(
        select(GroupMember, Profile)
        .join(Profile, Profile.id == GroupMember.profile_id)
        .where(GroupMember.group_id == group.id)
        .order_by(GroupMember.joined_at)
    ).all()
    result = []
    for membership, profile in members:
        count = (
            db.scalar(
                select(func.count())
                .select_from(UserMovieState)
                .where(
                    UserMovieState.profile_id == profile.id,
                    UserMovieState.preference.is_not(None),
                )
            )
            or 0
        )
        result.append(
            GroupMemberOut(
                profile_id=profile.id,
                name=profile.name,
                role=membership.role,
                feedback_count=count,
                needs_onboarding=count == 0,
            )
        )
    return GroupOut(
        id=group.id,
        name=group.name,
        owner_profile_id=group.owner_profile_id,
        recommendation_version=group.recommendation_version,
        members=result,
    )


def _invalidate_groups(db: Session, profile_id: int) -> None:
    for group in db.scalars(
        select(Group)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(GroupMember.profile_id == profile_id)
    ):
        group.recommendation_version += 1


def _movie_or_404(db: Session, movie_id: int) -> Movie:
    movie = db.get(Movie, movie_id)
    if not movie:
        raise HTTPException(status_code=404, detail="movie not found")
    return movie


def _attach_posters(db: Session, movies: list[Movie], settings: Settings) -> None:
    hydrate_posters(
        db,
        movies,
        settings.data_dir,
        enabled=settings.poster_lookup,
        live_fetch=settings.poster_live_fetch,
    )


def _cached_recommendation_rows(
    db: Session,
    cache: RecommendationCache,
    key: str,
    loader: Callable[[], list[tuple[Movie, float, str]]],
) -> list[tuple[Movie, float, str]]:
    cached = cache.get(key)
    if cached is None:
        rows = loader()
        cache.set(key, [(movie.id, score, reason) for movie, score, reason in rows])
        return rows
    movie_ids = [movie_id for movie_id, _score, _reason in cached]
    movie_map = {
        movie.id: movie
        for movie in db.scalars(select(Movie).where(Movie.id.in_(movie_ids)))
    }
    return [
        (movie_map[movie_id], score, reason)
        for movie_id, score, reason in cached
        if movie_id in movie_map
    ]


def _recommendations_out(
    db: Session,
    rows: list[tuple[Movie, float, str]],
    settings: Settings,
    *,
    request_id: str | None = None,
    impression_id: str | None = None,
    model_version: str = "content-hybrid-v1",
    feature_version: str = "taste-v1",
    policy_version: str = "none",
) -> list[RecommendationOut]:
    _attach_posters(db, [movie for movie, _score, _reason in rows], settings)
    return [
        _recommendation(
            movie,
            score,
            reason,
            request_id=request_id,
            impression_id=impression_id,
            model_version=model_version,
            feature_version=feature_version,
            policy_version=policy_version,
        )
        for movie, score, reason in rows
    ]


def _recommendation(
    movie: Movie,
    score: float,
    reason: str,
    *,
    request_id: str | None = None,
    impression_id: str | None = None,
    model_version: str = "content-hybrid-v1",
    feature_version: str = "taste-v1",
    policy_version: str = "none",
) -> RecommendationOut:
    return RecommendationOut(
        movie=MovieOut.model_validate(movie),
        score=score,
        reason=reason,
        reasons=[reason],
        request_id=request_id,
        impression_id=impression_id,
        model_version=model_version,
        feature_version=feature_version,
        policy_version=policy_version,
    )


def _log_personal_recommendations(
    db: Session,
    profile: Profile,
    pool: list[tuple[Movie, float, str]],
    limit: int,
    settings: Settings,
    user_id: int | None,
    bandit_policy: VWPolicy | None,
) -> list[RecommendationOut]:
    request_id = str(uuid.uuid4())
    impression_id = str(uuid.uuid4())
    decision = None
    selection_by_movie: dict[int, float] = {}
    if bandit_policy and pool:
        try:
            decision = bandit_policy.choose(
                {
                    "profile_version": float(profile.recommendation_version),
                    "candidate_count": float(len(pool)),
                },
                [
                    BanditAction(
                        movie_id=movie.id,
                        features={
                            "hybrid_score": score,
                            "popularity": movie.popularity_score or 0.0,
                            "year": float(movie.year or 0),
                            "runtime": float(movie.runtime or 0),
                            "primary_genre": (movie.genres or ["unknown"])[0],
                        },
                    )
                    for movie, score, _reason in pool
                ],
            )
            selection_by_movie = {
                movie.id: probability
                for (movie, _score, _reason), probability in zip(
                    pool, decision.probabilities, strict=True
                )
            }
            if not settings.bandit_shadow:
                chosen = next(row for row in pool if row[0].id == decision.movie_id)
                pool = [chosen, *(row for row in pool if row[0].id != decision.movie_id)]
        except (RuntimeError, ValueError):
            logger.exception("bandit_decision_failed")
            decision = None
    displayed = pool[:limit]
    embedding_model, feature_version = embedding_versions(db)
    model_version = (
        f"embedding-hybrid:{embedding_model}"
        if embedding_model != "none"
        else "genre-popularity-fallback-v1"
    )
    db.add(
        RecommendationImpression(
            request_id=request_id,
            impression_id=impression_id,
            user_id=user_id,
            context="personal",
            candidate_movie_ids=[movie.id for movie, _score, _reason in pool],
            displayed_movie_ids=[movie.id for movie, _score, _reason in displayed],
            displayed_positions=list(range(len(displayed))),
            model_version=model_version,
            feature_version=feature_version,
            policy_version=(
                decision.policy_version
                if decision and not settings.bandit_shadow
                else f"shadow:{decision.policy_version}"
                if decision
                else "none"
            ),
            experiment_version=assign_experiment(
                str(user_id or profile.id),
                "personal-policy-v1",
                {"control": 90, "vw-shadow": 10},
            ),
            selection_probabilities=(
                [selection_by_movie.get(movie.id) for movie, _score, _reason in displayed]
                if decision and not settings.bandit_shadow
                else [None] * len(displayed)
            ),
            policy_context=(
                {
                    "action_index": decision.action_index,
                    "probability": decision.probability,
                    "probabilities": list(decision.probabilities),
                    "example_lines": list(decision.example_lines),
                    "movie_id": decision.movie_id,
                    "shadow": settings.bandit_shadow,
                }
                if decision
                else None
            ),
        )
    )
    db.commit()
    return _recommendations_out(
        db,
        displayed,
        settings,
        request_id=request_id,
        impression_id=impression_id,
        model_version=model_version,
        feature_version=feature_version,
        policy_version=(
            decision.policy_version if decision and not settings.bandit_shadow else "none"
        ),
    )


def _evaluation_runs(artifact_dir: Path) -> list[ModelRunOut]:
    path = artifact_dir / "evaluation" / "results.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
        created_at = datetime.fromisoformat(payload["measurement"]["created_at_utc"])
        fingerprint = payload["dataset"]["fingerprint"]
        return [
            ModelRunOut(
                model_name=name,
                version=f"{name}-{fingerprint[:8]}",
                dataset_fingerprint=fingerprint,
                parameters=payload["models"].get(name, {}).get("config", {}),
                metrics=metrics,
                created_at=created_at,
            )
            for name, metrics in payload["test_metrics"].items()
        ]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        logger.exception("invalid_evaluation_artifact")
        return []


def _ndcg(run: ModelRunOut) -> float:
    value = run.metrics.get("ndcg_at_k", 0)
    return float(value) if isinstance(value, (int, float)) else 0.0


async def _broadcast_session(session_id: str, payload: dict[str, object]) -> None:
    disconnected = []
    for websocket in _session_sockets.get(session_id, set()):
        try:
            await websocket.send_json(payload)
        except (RuntimeError, WebSocketDisconnect):
            disconnected.append(websocket)
    for websocket in disconnected:
        _session_sockets.get(session_id, set()).discard(websocket)


app = create_app()
