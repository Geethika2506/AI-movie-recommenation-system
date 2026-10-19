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
};

export type Profile = {
  id: number;
  name: string;
  color: string;
  onboarded: boolean;
};

export type MovieAction = "watchlist" | "watched" | "dismiss";
export type SwipeAction = "liked" | "disliked" | "skip" | "watchlist";

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
