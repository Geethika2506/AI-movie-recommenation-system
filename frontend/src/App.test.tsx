import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { Profile } from "./types";

function renderApp(path = "/") {
  return render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);
}

const profile: Profile = {
  id: 1,
  name: "Maya",
  color: "#f6b84b",
  onboarded: true,
  seedPreferredGenres: ["Drama"],
};
const apiProfile = { id: 1, name: "Maya", seed_preferred_genres: ["Drama"], seed_disliked_genres: [] as string[] };
const auth = {
  access_token: "test-access-token",
  expires_at: "2030-01-01T00:00:00Z",
  user: {
    id: 1,
    username: "maya",
    email: "maya@example.com",
    country: null,
    streaming_provider_ids: [],
    created_at: "2026-01-01T00:00:00Z",
  },
};
const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  }));

const sampleMovie = {
  id: 1,
  title: "Arrival",
  year: 2016,
  genres: ["Sci-Fi"],
  overview: "First contact.",
  poster_path: null,
  poster_url: null,
  runtime: 116,
  language: "en",
};

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
    const url = String(input);
    if (url.includes("/auth/refresh")) return json(auth);
    if (url.includes("/auth/login") || url.includes("/auth/register")) return json(auth, 201);
    if (url.includes("/wishlist")) return json([]);
    if (url.includes("/recommendations")) {
      return json([{ movie: sampleMovie, score: 0.8, reason: "test", model_version: "hybrid-v1" }]);
    }
    if (url.includes("/profiles") && !url.includes("/preferences") && !url.includes("/recommendations") && !url.includes("/discover") && !url.includes("/wishlist") && !url.includes("/onboarding-preferences")) {
      return json([apiProfile]);
    }
    if (url.includes("/onboarding-preferences")) {
      return json({ ...apiProfile, seed_preferred_genres: ["Drama"], seed_disliked_genres: [] });
    }
    if (url.includes("/settings")) return json({
      active_model: "hybrid-v1", dataset: "test", movie_count: 10, rating_count: 0,
      last_trained: null, read_only: false, tmdb_available: false,
    });
    if (url.includes("/models")) return json([]);
    if (url.includes("/preferences/")) return json({
      movie_id: 1, action_id: 1, recommendation_version: 1,
    });
    return json({ detail: "test fallback" }, 500);
  }));
});

describe("CineMatch", () => {
  it("replaces stale cached profile ids with server profiles after login", async () => {
    localStorage.setItem(
      "cinematch:profiles",
      JSON.stringify([{ id: 999, name: "Maya", color: "#f6b84b", onboarded: true }]),
    );
    localStorage.setItem("cinematch:activeProfile", "999");
    vi.mocked(fetch).mockImplementation((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/auth/refresh")) return json(auth);
      if (url.includes("/profiles") && !url.includes("/999")) {
        return json([{ id: 2, name: "Maya", seed_preferred_genres: ["Drama"], seed_disliked_genres: [] }]);
      }
      if (url.includes("/profiles/999")) return json({ detail: "profile not found" }, 404);
      if (url.includes("/wishlist")) return json([]);
      if (url.includes("/profiles/2/recommendations")) {
        return json([{ movie: sampleMovie, score: 0.8, reason: "test", model_version: "hybrid-v1" }]);
      }
      if (url.includes("/settings")) {
        return json({
          active_model: "hybrid-v1", dataset: "test", movie_count: 10, rating_count: 0,
          last_trained: null, read_only: false, tmdb_available: false,
        });
      }
      return json({ detail: "test fallback" }, 500);
    });
    renderApp("/");
    expect(await screen.findByRole("option", { name: "Maya" })).toBeInTheDocument();
    expect(localStorage.getItem("cinematch:activeProfile")).toBe("2");
    expect(JSON.parse(localStorage.getItem("cinematch:profiles") ?? "[]")[0].id).toBe(2);
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/profiles/999"))).toBe(false);
  });

  it("requires authentication and registers a real account", async () => {
    vi.mocked(fetch).mockImplementation((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/auth/refresh")) return json({ detail: "not authenticated" }, 401);
      if (url.includes("/auth/register")) return json(auth, 201);
      if (url.includes("/profiles") && !url.includes("/onboarding-preferences")) {
        return json([{ id: 1, name: "Maya", seed_preferred_genres: [], seed_disliked_genres: [] }]);
      }
      if (url.includes("/settings")) return json({
        active_model: "hybrid-v1", dataset: "test", movie_count: 10, rating_count: 0,
        last_trained: null, read_only: false, tmdb_available: false,
      });
      return json({ detail: "test fallback" }, 500);
    });
    const user = userEvent.setup();
    renderApp();
    await user.click(await screen.findByRole("button", { name: "Need an account? Register" }));
    await user.type(screen.getByLabelText("Username"), "maya");
    await user.type(screen.getByLabelText("Profile name"), "Maya");
    await user.type(screen.getByLabelText("Email"), "maya@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: "Register" }));
    expect(await screen.findByRole("heading", { name: /What does Maya like/i })).toBeInTheDocument();
  });

  it("keeps swipe meanings distinct and allows finishing early", async () => {
    localStorage.setItem("cinematch:profiles", JSON.stringify([{ ...profile, onboarded: false, seedPreferredGenres: ["Drama"] }]));
    localStorage.setItem("cinematch:activeProfile", "1");
    const user = userEvent.setup();
    renderApp("/onboarding");
    await screen.findByRole("option", { name: "Maya" });
    const finish = await screen.findByRole("button", { name: /See my recommendations/ });
    await waitFor(() => expect(finish).toBeEnabled());
    expect(await screen.findByRole("button", { name: "← Dislike" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Skip/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Want to watch/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Watched/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Love/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Like →" }));
    expect(await screen.findByText("1 choices saved")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Undo latest" })).toBeEnabled();
  });

  it("filters search results with accessible controls", async () => {
    localStorage.setItem("cinematch:profiles", JSON.stringify([profile]));
    const user = userEvent.setup();
    renderApp("/search");
    const search = await screen.findByLabelText("Search by title");
    await user.type(search, "Arrival");
    expect(await screen.findByRole("heading", { name: "Arrival" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Parasite" })).not.toBeInTheDocument();
  });

  it("offers logout, recommendation reset, and account edits in settings", async () => {
    localStorage.setItem("cinematch:profiles", JSON.stringify([profile]));
    renderApp("/settings");
    expect(await screen.findByRole("heading", { name: "Account" })).toBeInTheDocument();
    expect(screen.getByLabelText("Username")).toHaveValue("maya");
    expect(screen.getByLabelText("Email")).toHaveValue("maya@example.com");
    expect(screen.getByRole("button", { name: "Email a code to change username" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Email a code to change email" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Email a code to change password" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reset recommendations" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Log out" }).length).toBeGreaterThan(1);
  });

  it("shows an empty want-to-watch state before anything is saved", async () => {
    localStorage.setItem("cinematch:profiles", JSON.stringify([profile]));
    vi.mocked(fetch).mockImplementation((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/auth/refresh")) return json(auth);
      if (url.includes("/wishlist")) return json([]);
      if (url.includes("/profiles") && !url.includes("/preferences")) return json([apiProfile]);
      if (url.includes("/settings")) return json({
        active_model: "hybrid-v1", dataset: "test", movie_count: 10, rating_count: 0,
        last_trained: null, read_only: false, tmdb_available: false,
      });
      return json({ detail: "test fallback" }, 500);
    });
    renderApp("/wishlist");
    expect(await screen.findByText("Nothing saved yet")).toBeInTheDocument();
  });
});
