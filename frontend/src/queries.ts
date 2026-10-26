import type { GroupRecommendationMode } from "./types";

export const queryKeys = {
  settings: ["settings"] as const,
  discover: (profileId: number) => ["discover", profileId] as const,
  recommendations: (profileId: number, discovery: number) =>
    ["recommendations", profileId, Math.round(discovery)] as const,
  wishlist: (profileId: number) => ["wishlist", profileId] as const,
  movies: (query: string, page: number) => ["movies", query, page] as const,
  movie: (id: number) => ["movie", id] as const,
  similar: (id: number) => ["similar", id] as const,
  groups: ["groups"] as const,
  rooms: (groupId: number, profileId: number) => ["rooms", groupId, profileId] as const,
  sessions: (groupId: number, profileId: number) => ["sessions", groupId, profileId] as const,
  sessionRecommendations: (
    sessionId: string,
    profileId: number,
    mode?: GroupRecommendationMode,
  ) => ["session-recommendations", sessionId, profileId, mode] as const,
  groupRecommendations: (
    groupId: number,
    profileId: number,
    strategy: string,
    includeWatched: boolean,
  ) => ["group-recommendations", groupId, profileId, strategy, includeWatched] as const,
  roomRecommendations: (
    groupId: number,
    roomId: number,
    profileId: number,
    strategy: string,
    includeWatched: boolean,
  ) => ["room-recommendations", groupId, roomId, profileId, strategy, includeWatched] as const,
};
