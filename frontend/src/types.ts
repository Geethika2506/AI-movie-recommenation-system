export type Movie = {
  id: number;
  title: string;
  year: number;
  genres: string[];
  language: string;
  runtime: number;
  score: number;
  reason: string;
  synopsis: string;
  posterUrl?: string;
  requestId?: string;
  impressionId?: string;
  policyVersion?: string;
};

export type Profile = {
  id: number;
  name: string;
  color: string;
  onboarded: boolean;
  seedPreferredGenres?: string[];
  seedDislikedGenres?: string[];
};

export type User = {
  id: number;
  username: string;
  email: string;
  country?: string;
  streamingProviderIds: number[];
};

export type MovieAction = "watchlist" | "watched" | "dismiss";
export type SwipeAction = "liked" | "disliked" | "love" | "skip" | "watchlist" | "watched" | "not_interested";

export type PreferenceResult = {
  movieId: number;
  actionId: number;
  recommendationVersion: number;
};

export type GroupMember = {
  profileId: number;
  name: string;
  role: string;
  feedbackCount: number;
  needsOnboarding: boolean;
};

export type Group = {
  id: number;
  name: string;
  ownerProfileId: number;
  recommendationVersion: number;
  members: GroupMember[];
};

export type GroupSession = {
  id: string;
  groupId: number;
  roomId?: number;
  status: string;
  participantProfileIds: number[];
  participantSetVersion: number;
  selectedMovieId?: number;
};

export type GroupRoomMember = {
  profileId: number;
  name: string;
  host: boolean;
};

export type GroupRoom = {
  id: number;
  groupId: number;
  inviteId: number;
  hostProfileId: number;
  hostName: string;
  name: string;
  members: GroupRoomMember[];
};

export type Evaluation = {
  model: string;
  precisionAt10: number;
  recallAt10: number;
  ndcgAt10: number;
  coverage: number;
  novelty: number;
};

export type Settings = {
  activeModel: string;
  dataset: string;
  movieCount: number;
  ratingCount: number;
  lastTrained?: string;
  readOnly: boolean;
  tmdbAvailable?: boolean;
};
