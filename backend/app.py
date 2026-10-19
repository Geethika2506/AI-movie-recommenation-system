import hashlib
import json
import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.catalog import import_prepared_catalog
from backend.config import Settings, get_settings
from backend.db import Base, make_engine, make_session_factory, session_dependency
from backend.models import (
    Group,
    GroupInvite,
    GroupMember,
    Interaction,
    InteractionKind,
    ModelRun,
    Movie,
    PreferenceEvent,
    Profile,
    Rating,
    UserMovieState,
)
from backend.posters import enrich_wikipedia_posters, hydrate_posters
from backend.recommendations import (
    discovery_for_profile,
    group_recommendations,
    recommend_for_profile,
    similar_movies,
)
from backend.schemas import (
    GroupCreate,
    GroupMemberOut,
    GroupOut,
    GroupRankingRequest,
    GroupRequest,
    InteractionIn,
    InviteCreate,
    InviteJoin,
    InviteOut,
    ModelRunOut,
    MovieOut,
    MoviePage,
    PreferenceStateOut,
    PreferenceUndo,
    PreferenceUpdate,
    ProfileCreate,
    ProfileOut,
    RatingIn,
    RatingOut,
    RecommendationOut,
    SystemMetadataOut,
)

logger = logging.getLogger("cinematch")
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
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
        yield
        engine.dispose()

    app = FastAPI(title="CineMatch API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = factory
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _register_middleware(app, settings)
    _register_routes(app, factory, settings)
    return app


def _register_middleware(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def request_controls(request: Request, call_next: object) -> Response:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
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

    @app.post("/api/profiles", response_model=ProfileOut, status_code=201)
    def create_profile(payload: ProfileCreate, request: Request, db: DB) -> Profile:
        profile = Profile(name=payload.name, owner_key_hash=_owner_hash(request))
        db.add(profile)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="profile name already exists") from exc
        db.refresh(profile)
        return profile

    @app.get("/api/profiles", response_model=list[ProfileOut])
    def list_profiles(request: Request, db: DB) -> list[Profile]:
        if settings.read_only:
            raise HTTPException(
                status_code=403,
                detail="profile enumeration is disabled in read-only demo mode",
            )
        return list(
            db.scalars(
                select(Profile)
                .where(Profile.owner_key_hash == _owner_hash(request))
                .order_by(Profile.name)
            )
        )

    @app.get("/api/movies", response_model=MoviePage)
    def search_movies(
        db: DB,
        q: str | None = Query(default=None, min_length=1, max_length=100),
        genre: str | None = Query(default=None, max_length=50),
        year: int | None = Query(default=None, ge=1870, le=2200),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=48, ge=1, le=100),
    ) -> MoviePage:
        query = select(Movie)
        if q:
            query = query.where(Movie.title.ilike(f"%{_escape_like(q)}%", escape="\\"))
        if genre:
            query = query.where(Movie.genres.contains([genre]))
        if year:
            query = query.where(Movie.year == year)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        movies = list(
            db.scalars(
                query.order_by(Movie.title).offset((page - 1) * page_size).limit(page_size)
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

        previous = _state_snapshot(state)
        if payload.action != "skip":
            if not state:
                state = UserMovieState(profile_id=profile_id, movie_id=movie_id)
                db.add(state)
            if payload.action in {"liked", "disliked"}:
                state.watched = True
                state.preference = payload.action
                state.watchlist = False
            elif payload.action == "watchlist":
                state.watchlist = True
            profile.recommendation_version += 1
            _invalidate_groups(db, profile.id)
        event = PreferenceEvent(
            profile_id=profile_id,
            movie_id=movie_id,
            request_id=payload.request_id,
            action=payload.action,
            previous_state=previous,
            new_state=_state_snapshot(state),
        )
        db.add(event)
        db.commit()
        db.refresh(event)
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
        _owned_profile_or_404(db, profile_id, request)
        return _recommendations_out(
            db,
            recommend_for_profile(db, profile_id, limit, discovery=discovery / 100),
            settings,
        )

    @app.get("/api/profiles/{profile_id}/discover", response_model=list[RecommendationOut])
    def discover(
        profile_id: int,
        request: Request,
        db: DB,
        limit: int = Query(default=60, ge=1, le=100),
    ) -> list[RecommendationOut]:
        _owned_profile_or_404(db, profile_id, request)
        return _recommendations_out(
            db, discovery_for_profile(db, profile_id, limit), settings
        )

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
        owner_hash = _owner_hash(request)
        groups = db.scalars(
            select(Group)
            .join(GroupMember, GroupMember.group_id == Group.id)
            .join(Profile, Profile.id == GroupMember.profile_id)
            .where(Profile.owner_key_hash == owner_hash)
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
            expires_at=datetime.now(UTC) + timedelta(hours=payload.expires_in_hours),
        )
        db.add(invite)
        db.commit()
        db.refresh(invite)
        return InviteOut(
            id=invite.id,
            token=token,
            invite_url=f"{settings.public_app_url.rstrip('/')}/invite/{token}",
            expires_at=invite.expires_at,
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

    @app.post("/api/group-invites/{token}/join", response_model=GroupOut)
    def join_group(
        token: str,
        payload: InviteJoin,
        request: Request,
        db: DB,
    ) -> GroupOut:
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
            db.commit()
        return _group_output(db, group)

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
        _group_member_or_404(db, group_id, requester.id)
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
        return _recommendations_out(
            db,
            group_recommendations(
                db,
                profile_ids,
                payload.limit,
                mean_weight=mean_weight,
                include_watched=payload.include_watched,
                genres=payload.genres,
                language=payload.language,
                max_runtime=payload.max_runtime,
            ),
            settings,
        )

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


def _owned_profile_or_404(db: Session, profile_id: int, request: Request) -> Profile:
    profile = _profile_or_404(db, profile_id)
    if not secrets.compare_digest(profile.owner_key_hash, _owner_hash(request)):
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
    )


def _recommendations_out(
    db: Session,
    rows: list[tuple[Movie, float, str]],
    settings: Settings,
) -> list[RecommendationOut]:
    _attach_posters(db, [movie for movie, _score, _reason in rows], settings)
    return [_recommendation(movie, score, reason) for movie, score, reason in rows]


def _recommendation(movie: Movie, score: float, reason: str) -> RecommendationOut:
    return RecommendationOut(
        movie=MovieOut.model_validate(movie),
        score=score,
        reason=reason,
        reasons=[reason],
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


app = create_app()
