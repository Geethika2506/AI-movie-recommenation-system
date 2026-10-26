import { movies, settings } from "./data";
import type { Evaluation, Group, GroupRecommendationMode, GroupRoom, GroupSession, Movie, MovieAction, PreferenceResult, Profile, Settings, SwipeAction, User } from "./types";

const API_URL = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "/api";
const demoFallback = import.meta.env.VITE_DEMO_FALLBACK === "true" || import.meta.env.MODE === "test";

const ACCESS_TOKEN_KEY = "cinematch:accessToken";
let accessToken = sessionStorage.getItem(ACCESS_TOKEN_KEY) ?? "";
let refreshPromise: Promise<ApiAuth> | null = null;

function persistAccessToken(token: string) {
  accessToken = token;
  sessionStorage.setItem(ACCESS_TOKEN_KEY, token);
}

function clearAccessToken() {
  accessToken = "";
  sessionStorage.removeItem(ACCESS_TOKEN_KEY);
}

const sessionKey = (() => {
  const existing = localStorage.getItem("cinematch:session");
  if (existing) return existing;
  const created = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}-${Math.random()}`;
  localStorage.setItem("cinematch:session", created);
  return created;
})();

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

function errorDetail(detail: unknown): string | undefined {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .filter((item) => item && typeof item === "object" && "msg" in item)
      .map((item) => (item as { msg?: string }).msg)
      .filter(Boolean) as string[];
    if (messages.length) return messages.join(" ");
  }
  return undefined;
}

async function rawRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-Cinematch-Session": sessionKey,
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw new ApiError(errorDetail(body?.detail) ?? `Request failed (${response.status})`, response.status);
  }
  return response.status === 204 ? (undefined as T) : await response.json() as T;
}

function refreshAccessToken(): Promise<ApiAuth> {
  if (!refreshPromise) {
    refreshPromise = rawRequest<ApiAuth>("/auth/refresh", { method: "POST" })
      .then((result) => {
        persistAccessToken(result.access_token);
        return result;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

async function request<T>(path: string, init?: RequestInit, fallback?: T): Promise<T> {
  try {
    try {
      return await rawRequest<T>(path, init);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401 && !path.startsWith("/auth/")) {
        await refreshAccessToken();
        return await rawRequest<T>(path, init);
      }
      throw error;
    }
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
  request_id?: string; impression_id?: string; policy_version?: string;
};
type ApiProfile = {
  id: number;
  name: string;
  seed_preferred_genres?: string[];
  seed_disliked_genres?: string[];
};
type ApiUser = {
  id: number; username: string; email: string; country: string | null;
  streaming_provider_ids: number[];
};
type ApiAuth = { access_token: string; expires_at: string; user: ApiUser };
type ApiMoviePage = { items: ApiMovie[]; total: number };
type ApiModelRun = { model_name: string; metrics: Record<string, number> };
type ApiPreference = {
  movie_id: number; action_id: number; recommendation_version: number;
};
type ApiGroup = {
  id: number; name: string; owner_profile_id: number; recommendation_version: number;
  members: { profile_id: number; name: string; role: string; feedback_count: number; needs_onboarding: boolean }[];
};
type ApiGroupSession = {
  id: string; group_id: number; room_id?: number | null; status: string; participant_profile_ids: number[];
  participant_set_version: number; selected_movie_id: number | null;
  ranking_algorithm: GroupRecommendationMode; consensus_policy: string;
};
type ApiRoom = {
  id: number; group_id: number; invite_id: number; host_profile_id: number; host_name: string; name: string;
  members: { profile_id: number; name: string; host: boolean }[];
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
const mapGroupSession = (session: ApiGroupSession): GroupSession => ({
  id: session.id,
  groupId: session.group_id,
  roomId: session.room_id ?? undefined,
  status: session.status,
  rankingAlgorithm: session.ranking_algorithm,
  consensusPolicy: session.consensus_policy,
  participantProfileIds: session.participant_profile_ids,
  participantSetVersion: session.participant_set_version,
  selectedMovieId: session.selected_movie_id ?? undefined,
});
const mapRoom = (room: ApiRoom): GroupRoom => ({
  id: room.id,
  groupId: room.group_id,
  inviteId: room.invite_id,
  hostProfileId: room.host_profile_id,
  hostName: room.host_name,
  name: room.name,
  members: room.members.map((member) => ({
    profileId: member.profile_id,
    name: member.name,
    host: member.host,
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
  requestId: recommendation?.request_id,
  impressionId: recommendation?.impression_id,
  policyVersion: recommendation?.policy_version,
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

const profileColors = ["#f6b84b", "#77cbb9", "#dd7d91", "#9b8ee8"];

const mapProfile = (profile: ApiProfile, index = 0): Profile => ({
  id: profile.id,
  name: profile.name,
  color: profileColors[index % profileColors.length],
  onboarded: false,
  seedPreferredGenres: profile.seed_preferred_genres ?? [],
  seedDislikedGenres: profile.seed_disliked_genres ?? [],
});

const mergeProfileMetadata = (
  server: Profile,
  local: Profile | undefined,
  index: number,
): Profile => ({
  id: server.id,
  name: server.name,
  color: local?.color ?? profileColors[index % profileColors.length],
  onboarded: local?.onboarded ?? false,
  seedPreferredGenres: server.seedPreferredGenres ?? [],
  seedDislikedGenres: server.seedDislikedGenres ?? [],
});

/** Replace stale browser profile IDs with the authenticated user's server profiles. */
async function syncProfilesWithServer(localProfiles: Profile[]): Promise<Profile[]> {
  let remote: ApiProfile[];
  try {
    remote = await rawRequest<ApiProfile[]>("/profiles");
  } catch {
    return [];
  }
  const localsById = new Map(localProfiles.filter((profile) => profile.id > 0).map((profile) => [profile.id, profile]));
  const localsByName = new Map(localProfiles.map((profile) => [profile.name.toLowerCase(), profile]));
  const merged: Profile[] = remote.map((profile, index) => {
    const server = mapProfile(profile, index);
    const local =
      localsById.get(profile.id) ?? localsByName.get(profile.name.toLowerCase());
    return mergeProfileMetadata(server, local, index);
  });
  const mergedNames = new Set(merged.map((profile) => profile.name.toLowerCase()));
  for (const local of localProfiles) {
    if (local.id > 0 && remote.some((profile) => profile.id === local.id)) {
      continue;
    }
    if (mergedNames.has(local.name.toLowerCase())) {
      continue;
    }
    if (local.id > 0) {
      continue;
    }
    try {
      const created = mapProfile(
        await rawRequest<ApiProfile>("/profiles", {
          method: "POST",
          body: JSON.stringify({ name: local.name }),
        }),
        merged.length,
      );
      merged.push({ ...created, color: local.color, onboarded: local.onboarded });
      mergedNames.add(local.name.toLowerCase());
    } catch {
      // Keep going if a pending local profile cannot be created yet.
    }
  }
  return merged;
}
const mapUser = (user: ApiUser): User => ({
  id: user.id,
  username: user.username,
  email: user.email,
  country: user.country ?? undefined,
  streamingProviderIds: user.streaming_provider_ids,
});

export const api = {
  register: (username: string, email: string, password: string, profileName: string) =>
    request<ApiAuth>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, email, password, profile_name: profileName }),
    }).then((result) => {
      persistAccessToken(result.access_token);
      return mapUser(result.user);
    }),
  login: (email: string, password: string) =>
    request<ApiAuth>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }).then((result) => {
      persistAccessToken(result.access_token);
      return mapUser(result.user);
    }),
  restoreSession: async () => {
    accessToken = sessionStorage.getItem(ACCESS_TOKEN_KEY) ?? "";
    if (accessToken) {
      try {
        return await rawRequest<ApiUser>("/users/me").then(mapUser);
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 401) throw error;
      }
    }
    try {
      return mapUser((await refreshAccessToken()).user);
    } catch (error) {
      clearAccessToken();
      throw error;
    }
  },
  logout: () => request<void>("/auth/logout", { method: "POST" }).finally(() => {
    clearAccessToken();
  }),
  me: () => request<ApiUser>("/users/me").then(mapUser),
  updateServices: (country: string, providerIds: number[]) =>
    request<ApiUser>("/users/me", {
      method: "PATCH",
      body: JSON.stringify({ country, streaming_provider_ids: providerIds }),
    }).then(mapUser),
  resetRecommendations: () =>
    request<void>("/users/me/recommendations/reset", { method: "POST" }),
  requestVerification: (body: {
    purpose: "username" | "email" | "password";
    current_password: string;
    username?: string;
    email?: string;
    password?: string;
  }) => request<{
    purpose: "username" | "email" | "password";
    sent_to: string;
    expires_at: string;
    delivery: "email" | "preview";
    preview_code?: string | null;
  }>("/users/me/verification", { method: "POST", body: JSON.stringify(body) }),
  confirmVerification: (purpose: "username" | "email" | "password", code: string) =>
    request<ApiUser>("/users/me/verification/confirm", {
      method: "POST",
      body: JSON.stringify({ purpose, code }),
    }).then(mapUser),
  movies: async (query = "", page = 1, pageSize = 48) => {
    const fallback = movies.filter((movie) => movie.title.toLowerCase().includes(query.toLowerCase()));
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (query) params.set("q", query);
    const path = `/movies?${params}`;
    const load = async () => {
      const result = await request<ApiMoviePage | Movie[]>(path, undefined, fallback);
      return Array.isArray(result) ? result : result.items.map((movie) => mapMovie(movie));
    };
    return load();
  },
  recommendations: (profileIds: number[], discovery: number, includeWatched = false) => {
    if (!profileIds.length || profileIds.some((id) => id <= 0)) {
      return Promise.resolve([...movies].slice(discovery > 55 ? 2 : 0));
    }
    if (profileIds.length > 1) {
      return request<ApiRecommendation[]>("/recommendations/group", {
        method: "POST",
        body: JSON.stringify({ profile_ids: profileIds.map(Number), limit: 48, include_watched: includeWatched }),
      }).then((rows) => withMatchScores(rows.map((row) => mapMovie(row.movie, row))));
    }
    return request<ApiRecommendation[]>(
      `/profiles/${profileIds[0]}/recommendations?limit=48&discovery=${Math.round(discovery)}`,
    ).then((rows) => withMatchScores(rows.map((row) => mapMovie(row.movie, row))));
  },
  discover: (profileId: number) =>
    profileId <= 0
      ? Promise.resolve(movies)
      : request<ApiRecommendation[]>(`/profiles/${profileId}/discover?limit=60`).then((rows) =>
          withMatchScores(rows.map((row) => mapMovie(row.movie, row))),
        ),
  movie: (id: number) => request<ApiMovie>(`/movies/${id}`, undefined, undefined)
    .then(mapMovie)
    .catch(() => movies.find((movie) => movie.id === id) ?? movies[0]),
  similar: (id: number) => request<ApiRecommendation[]>(`/movies/${id}/similar`, undefined, [])
    .then((rows) => rows.length ? rows.map((row) => mapMovie(row.movie, row)) : movies.filter((movie) => movie.id !== id).slice(0, 5)),
  profiles: () => request<ApiProfile[] | Profile[]>("/profiles", undefined, savedProfiles())
    .then((profiles) => profiles.map((profile, index) =>
      "color" in profile ? profile : mapProfile(profile, index)
    )),
  syncProfiles: syncProfilesWithServer,
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
  saveOnboardingPreferences: (profileId: number, preferredGenres: string[], dislikedGenres: string[]) =>
    request<ApiProfile>(`/profiles/${profileId}/onboarding-preferences`, {
      method: "PUT",
      body: JSON.stringify({
        preferred_genres: preferredGenres,
        disliked_genres: dislikedGenres,
      }),
    }).then(mapProfile),
  rate: (profileId: number, movieId: number, rating: number) =>
    request<void>(`/profiles/${profileId}/ratings/${movieId}`, { method: "PUT", body: JSON.stringify({ value: rating }) }, undefined),
  act: (profileId: number, movieId: number, action: MovieAction) =>
    request<void>(`/profiles/${profileId}/interactions/${action === "dismiss" ? "dismissed" : action}/${movieId}`, { method: "PUT", body: JSON.stringify({ active: true }) }, undefined),
  wishlist: (profileId: number) =>
    profileId <= 0
      ? Promise.resolve([] as Movie[])
      : request<ApiMovie[]>(`/profiles/${profileId}/wishlist`).then((movies) => movies.map((movie) => mapMovie(movie))),
  addToWishlist: (profileId: number, movie: Movie, requestId = crypto.randomUUID()) =>
    request<ApiPreference>(`/profiles/${profileId}/preferences/${movie.id}`, {
      method: "PUT",
      body: JSON.stringify({
        action: "watchlist",
        request_id: requestId,
        origin: "organic",
      }),
    }, { movie_id: movie.id, action_id: Date.now(), recommendation_version: 0 }),
  removeFromWishlist: (profileId: number, movieId: number) =>
    request<void>(`/profiles/${profileId}/wishlist/${movieId}`, { method: "DELETE" }),
  preference: (profileId: number, movie: Movie, action: SwipeAction, requestId: string): Promise<PreferenceResult> =>
    request<ApiPreference>(`/profiles/${profileId}/preferences/${movie.id}`, {
      method: "PUT",
      body: JSON.stringify({
        action,
        request_id: requestId,
        origin: movie.policyVersion && movie.policyVersion !== "none"
          ? "bandit_policy"
          : movie.requestId ? "recommendation" : "organic",
        recommendation_request_id: movie.requestId,
        impression_id: movie.impressionId,
      }),
    }, { movie_id: movie.id, action_id: Date.now(), recommendation_version: 0 }).then((result) => {
      return {
        movieId: result.movie_id,
        actionId: result.action_id,
        recommendationVersion: result.recommendation_version,
      };
    }),
  undoPreference: (profileId: number, action: PreferenceResult) =>
    request<ApiPreference>(`/profiles/${profileId}/preferences/${action.movieId}/undo`, {
      method: "POST",
      body: JSON.stringify({ action_id: action.actionId }),
    }, { movie_id: action.movieId, action_id: action.actionId, recommendation_version: action.recommendationVersion }),
  groups: () => request<ApiGroup[]>("/groups", undefined, []).then((items) => items.map(mapGroup)),
  createGroup: (name: string, profileId: number) =>
    request<ApiGroup>("/groups", { method: "POST", body: JSON.stringify({ name, profile_id: profileId }) }).then(mapGroup),
  createInvite: (groupId: number, profileId: number) =>
    request<{ id: number; token: string; invite_url: string; expires_at: string; room_id: number; room_name: string }>(`/groups/${groupId}/invites`, {
      method: "POST",
      body: JSON.stringify({ profile_id: profileId, expires_in_hours: 48 }),
    }),
  revokeInvite: (groupId: number, inviteId: number, profileId: number) =>
    request<void>(`/groups/${groupId}/invites/${inviteId}?profile_id=${profileId}`, { method: "DELETE" }),
  removeGroupMember: (groupId: number, memberProfileId: number, requesterProfileId: number) =>
    request<void>(`/groups/${groupId}/members/${memberProfileId}?requester_profile_id=${requesterProfileId}`, { method: "DELETE" }),
  joinGroup: (token: string, profileId: number) =>
    request<ApiGroup & { joined_room_id?: number; joined_room_name?: string }>(`/group-invites/${encodeURIComponent(token)}/join`, {
      method: "POST",
      body: JSON.stringify({ profile_id: profileId }),
    }).then((group) => ({
      group: mapGroup(group),
      roomId: group.joined_room_id,
      roomName: group.joined_room_name,
    })),
  groupRooms: (groupId: number, profileId: number) =>
    request<ApiRoom[]>(`/groups/${groupId}/rooms?profile_id=${profileId}`, undefined, []).then((rooms) => rooms.map(mapRoom)),
  roomInviteLink: (groupId: number, roomId: number, profileId: number) =>
    request<{ invite_url: string; expires_at: string; room_id: number; room_name: string }>(
      `/groups/${groupId}/rooms/${roomId}/invite-link?profile_id=${profileId}`,
    ),
  deleteRoom: (groupId: number, roomId: number, profileId: number) =>
    request<void>(`/groups/${groupId}/rooms/${roomId}?profile_id=${profileId}`, { method: "DELETE" }),
  removeRoomMember: (
    groupId: number,
    roomId: number,
    memberProfileId: number,
    requesterProfileId: number,
  ) =>
    request<void>(
      `/groups/${groupId}/rooms/${roomId}/members/${memberProfileId}?requester_profile_id=${requesterProfileId}`,
      { method: "DELETE" },
    ),
  groupRecommendations: (groupId: number, profileId: number, strategy: "balanced" | "everyone_matters", includeWatched: boolean) =>
    request<ApiRecommendation[]>(`/groups/${groupId}/recommendations`, {
      method: "POST",
      body: JSON.stringify({ requester_profile_id: profileId, strategy, include_watched: includeWatched, limit: 40 }),
    }).then((rows) => withMatchScores(rows.map((row) => mapMovie(row.movie, row)))),
  createGroupSession: (
    groupId: number,
    requesterProfileId: number,
    participantProfileIds: number[],
    roomId: number | undefined,
    rankingAlgorithm: GroupRecommendationMode,
  ) =>
    request<ApiGroupSession>(`/groups/${groupId}/sessions`, {
      method: "POST",
      body: JSON.stringify({
        requester_profile_id: requesterProfileId,
        participant_profile_ids: participantProfileIds,
        room_id: roomId,
        consensus_policy: "majority",
        ranking_algorithm: rankingAlgorithm,
      }),
    }).then(mapGroupSession),
  roomRecommendations: (groupId: number, roomId: number, profileId: number, strategy: "balanced" | "everyone_matters", includeWatched: boolean) =>
    request<ApiRecommendation[]>(`/groups/${groupId}/rooms/${roomId}/recommendations`, {
      method: "POST",
      body: JSON.stringify({ requester_profile_id: profileId, strategy, include_watched: includeWatched, limit: 40 }),
    }).then((rows) => withMatchScores(rows.map((row) => mapMovie(row.movie, row)))),
  groupSessions: (groupId: number, profileId: number) =>
    request<ApiGroupSession[]>(`/groups/${groupId}/sessions?profile_id=${profileId}`)
      .then((sessions) => sessions.map(mapGroupSession)),
  updateGroupSessionRanking: (
    sessionId: string,
    requesterProfileId: number,
    rankingAlgorithm: GroupRecommendationMode,
  ) =>
    request<ApiGroupSession>(`/sessions/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify({
        requester_profile_id: requesterProfileId,
        ranking_algorithm: rankingAlgorithm,
      }),
    }).then(mapGroupSession),
  sessionRecommendations: (sessionId: string, profileId: number) =>
    request<ApiRecommendation[]>(`/sessions/${sessionId}/recommendations?profile_id=${profileId}`)
      .then((rows) => withMatchScores(rows.map((row) => mapMovie(row.movie, row)))),
  groupVote: (sessionId: string, profileId: number, movieId: number, action: "like" | "love" | "dislike" | "veto" | "skip") =>
    request(`/sessions/${sessionId}/votes`, {
      method: "POST",
      body: JSON.stringify({
        profile_id: profileId, movie_id: movieId, action, request_id: crypto.randomUUID(),
      }),
    }),
  finalizeSession: (sessionId: string, profileId: number, movieId: number) =>
    request<ApiGroupSession>(`/sessions/${sessionId}/finalize`, {
      method: "POST",
      body: JSON.stringify({ requester_profile_id: profileId, movie_id: movieId }),
    }).then(mapGroupSession),
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

