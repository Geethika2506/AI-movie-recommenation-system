import { movies, settings } from "./data";
import type { Evaluation, Group, Movie, MovieAction, PreferenceResult, Profile, Settings, SwipeAction } from "./types";

const API_URL = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "/api";
const demoFallback = import.meta.env.VITE_DEMO_FALLBACK !== "false";
const sessionKey = (() => {
  const existing = localStorage.getItem("cinematch:session");
  if (existing) return existing;
  const created = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}-${Math.random()}`;
  localStorage.setItem("cinematch:session", created);
  return created;
})();

class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit, fallback?: T): Promise<T> {
  try {
    const response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", "X-Cinematch-Session": sessionKey, ...init?.headers },
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => null) as { detail?: string } | null;
      throw new ApiError(detail?.detail ?? `Request failed (${response.status})`, response.status);
    }
    return response.status === 204 ? (undefined as T) : await response.json() as T;
  } catch (error) {
    if (demoFallback && (fallback !== undefined || init?.method) && (!(error instanceof ApiError) || error.status >= 500)) {
      return fallback as T;
    }
    throw error;
  }
}

const savedProfiles = (): Profile[] => {
  try {
    return JSON.parse(localStorage.getItem("cinematch:profiles") ?? "[]") as Profile[];
  } catch {
    return [];
  }
};

type ApiMovie = {
  id: number; title: string; year: number | null; genres: string[];
  overview: string | null; poster_path: string | null; poster_url: string | null;
  runtime: number | null; language: string | null;
};
type ApiRecommendation = {
  movie: ApiMovie; score: number; reason: string; model_version: string;
};
type ApiProfile = { id: number; name: string };
type ApiMoviePage = { items: ApiMovie[]; total: number };
type ApiModelRun = { model_name: string; metrics: Record<string, number> };
type ApiPreference = {
  movie_id: number; action_id: number; recommendation_version: number;
};
type ApiGroup = {
  id: number; name: string; owner_profile_id: number; recommendation_version: number;
  members: { profile_id: number; name: string; role: string; feedback_count: number; needs_onboarding: boolean }[];
};
const mapGroup = (group: ApiGroup): Group => ({
  id: group.id,
  name: group.name,
  ownerProfileId: group.owner_profile_id,
  recommendationVersion: group.recommendation_version,
  members: group.members.map((member) => ({
    profileId: member.profile_id,
    name: member.name,
    role: member.role,
    feedbackCount: member.feedback_count,
    needsOnboarding: member.needs_onboarding,
  })),
});

const mapMovie = (movie: ApiMovie, recommendation?: ApiRecommendation): Movie => ({
  id: movie.id,
  title: movie.title,
  year: movie.year ?? 0,
  genres: movie.genres,
  language: movie.language ?? "",
  runtime: movie.runtime ?? 0,
  score: recommendation?.score ?? 0,
  reason: recommendation?.reason ?? "",
  synopsis: movie.overview ?? "Extended metadata is not available for this title.",
  posterUrl: movie.poster_url ?? undefined,
});

const withMatchScores = (movies: Movie[]): Movie[] => {
  if (!movies.length) return movies;
  const values = movies.map((movie) => movie.score);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = high - low;
  return movies.map((movie) => ({
    ...movie,
    score: span < 1e-9 ? 72 : Math.round(58 + ((movie.score - low) / span) * 37),
  }));
};

const mapProfile = (profile: ApiProfile, index = 0): Profile => ({
  id: profile.id,
  name: profile.name,
  color: ["#f6b84b", "#77cbb9", "#dd7d91", "#9b8ee8"][index % 4],
  onboarded: false,
});

export const api = {
  movies: async (query = "", page = 1, pageSize = 48) => {
    const fallback = movies.filter((movie) => movie.title.toLowerCase().includes(query.toLowerCase()));
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (query) params.set("q", query);
    const result = await request<ApiMoviePage | Movie[]>(`/movies?${params}`, undefined, fallback);
    return Array.isArray(result) ? result : result.items.map((movie) => mapMovie(movie));
  },
  recommendations: (profileIds: number[], discovery: number, includeWatched = false) => {
    if (!profileIds.length || profileIds.some((id) => id <= 0)) {
      return Promise.resolve([...movies].slice(discovery > 55 ? 2 : 0));
    }
    return profileIds.length > 1
      ? request<ApiRecommendation[]>("/recommendations/group", {
          method: "POST",
          body: JSON.stringify({ profile_ids: profileIds.map(Number), limit: 48, include_watched: includeWatched }),
        }).then((rows) => withMatchScores(rows.map((row) => mapMovie(row.movie, row))))
      : request<ApiRecommendation[]>(
          `/profiles/${profileIds[0]}/recommendations?limit=48&discovery=${Math.round(discovery)}`,
        ).then((rows) => withMatchScores(rows.map((row) => mapMovie(row.movie, row))));
  },
  discover: (profileId: number) =>
    profileId <= 0
      ? Promise.resolve(movies)
      : request<ApiRecommendation[]>(`/profiles/${profileId}/discover?limit=60`)
          .then((rows) => withMatchScores(rows.map((row) => mapMovie(row.movie, row)))),
  movie: (id: number) => request<ApiMovie>(`/movies/${id}`, undefined, undefined)
    .then(mapMovie)
    .catch(() => movies.find((movie) => movie.id === id) ?? movies[0]),
  similar: (id: number) => request<ApiRecommendation[]>(`/movies/${id}/similar`, undefined, [])
    .then((rows) => rows.length ? rows.map((row) => mapMovie(row.movie, row)) : movies.filter((movie) => movie.id !== id).slice(0, 5)),
  profiles: () => request<ApiProfile[] | Profile[]>("/profiles", undefined, savedProfiles())
    .then((profiles) => profiles.map((profile, index) =>
      "color" in profile ? profile : mapProfile(profile, index)
    )),
  saveProfiles: (profiles: Profile[]) => {
    localStorage.setItem("cinematch:profiles", JSON.stringify(profiles));
  },
  createProfile: async (name: string) => {
    try {
      return mapProfile(await request<ApiProfile>("/profiles", { method: "POST", body: JSON.stringify({ name }) }));
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const existing = (await request<ApiProfile[]>("/profiles", undefined, [])).find((profile) => profile.name === name);
        if (existing) return mapProfile(existing);
      }
      throw error;
    }
  },
  rate: (profileId: number, movieId: number, rating: number) =>
    request<void>(`/profiles/${profileId}/ratings/${movieId}`, { method: "PUT", body: JSON.stringify({ value: rating }) }, undefined),
  act: (profileId: number, movieId: number, action: MovieAction) =>
    request<void>(`/profiles/${profileId}/interactions/${action === "dismiss" ? "dismissed" : action}/${movieId}`, { method: "PUT", body: JSON.stringify({ active: true }) }, undefined),
  preference: (profileId: number, movieId: number, action: SwipeAction, requestId: string): Promise<PreferenceResult> =>
    request<ApiPreference>(`/profiles/${profileId}/preferences/${movieId}`, {
      method: "PUT",
      body: JSON.stringify({ action, request_id: requestId }),
    }, { movie_id: movieId, action_id: Date.now(), recommendation_version: 0 }).then((result) => ({
      movieId: result.movie_id,
      actionId: result.action_id,
      recommendationVersion: result.recommendation_version,
    })),
  undoPreference: (profileId: number, action: PreferenceResult) =>
    request<ApiPreference>(`/profiles/${profileId}/preferences/${action.movieId}/undo`, {
      method: "POST",
      body: JSON.stringify({ action_id: action.actionId }),
    }, { movie_id: action.movieId, action_id: action.actionId, recommendation_version: action.recommendationVersion }),
  groups: () => request<ApiGroup[]>("/groups", undefined, []).then((items) => items.map(mapGroup)),
  createGroup: (name: string, profileId: number) =>
    request<ApiGroup>("/groups", { method: "POST", body: JSON.stringify({ name, profile_id: profileId }) }).then(mapGroup),
  createInvite: (groupId: number, profileId: number) =>
    request<{ id: number; token: string; invite_url: string; expires_at: string }>(`/groups/${groupId}/invites`, {
      method: "POST",
      body: JSON.stringify({ profile_id: profileId, expires_in_hours: 48 }),
    }),
  revokeInvite: (groupId: number, inviteId: number, profileId: number) =>
    request<void>(`/groups/${groupId}/invites/${inviteId}?profile_id=${profileId}`, { method: "DELETE" }),
  removeGroupMember: (groupId: number, memberProfileId: number, requesterProfileId: number) =>
    request<void>(`/groups/${groupId}/members/${memberProfileId}?requester_profile_id=${requesterProfileId}`, { method: "DELETE" }),
  joinGroup: (token: string, profileId: number) =>
    request<ApiGroup>(`/group-invites/${encodeURIComponent(token)}/join`, {
      method: "POST",
      body: JSON.stringify({ profile_id: profileId }),
    }).then(mapGroup),
  groupRecommendations: (groupId: number, profileId: number, strategy: "balanced" | "everyone_matters", includeWatched: boolean) =>
    request<ApiRecommendation[]>(`/groups/${groupId}/recommendations`, {
      method: "POST",
      body: JSON.stringify({ requester_profile_id: profileId, strategy, include_watched: includeWatched, limit: 40 }),
    }).then((rows) => withMatchScores(rows.map((row) => mapMovie(row.movie, row)))),
  evaluation: () => request<ApiModelRun[]>("/models", undefined, []).then((runs): Evaluation[] =>
    [...runs].sort((left, right) => (right.metrics.ndcg_at_k ?? 0) - (left.metrics.ndcg_at_k ?? 0)).map((run) => ({
      model: run.model_name,
      precisionAt10: run.metrics.precision_at_k ?? 0,
      recallAt10: run.metrics.recall_at_k ?? 0,
      ndcgAt10: run.metrics.ndcg_at_k ?? 0,
      coverage: run.metrics.coverage ?? 0,
      novelty: 1 - (run.metrics.popularity_bias ?? 0),
    }))),
  settings: () => request<{
    active_model: string; dataset: string; movie_count: number; rating_count: number;
    last_trained: string | null; read_only: boolean; tmdb_available: boolean;
  }>("/settings", undefined, undefined).then((value): Settings => ({
    activeModel: value.active_model,
    dataset: value.dataset,
    movieCount: value.movie_count,
    ratingCount: value.rating_count,
    lastTrained: value.last_trained ?? undefined,
    readOnly: value.read_only,
    tmdbAvailable: value.tmdb_available,
  })).catch(() => settings),
};

export { ApiError };
