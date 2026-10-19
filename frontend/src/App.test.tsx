import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import App from "./App";
import type { Profile } from "./types";

function renderApp(path = "/") {
  return render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);
}

const profile: Profile = { id: 1, name: "Maya", color: "#f6b84b", onboarded: true };

describe("CineMatch", () => {
  it("creates a local profile and starts onboarding", async () => {
    const user = userEvent.setup();
    renderApp();
    await user.type(screen.getByLabelText("What should we call you?"), "Maya");
    await user.click(screen.getByRole("button", { name: "Create profile" }));
    expect(await screen.findByRole("heading", { name: "What have you watched?" })).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("cinematch:profiles") ?? "[]")).toHaveLength(1);
  });

  it("keeps swipe meanings distinct and allows finishing early", async () => {
    localStorage.setItem("cinematch:profiles", JSON.stringify([{ ...profile, onboarded: false }]));
    localStorage.setItem("cinematch:activeProfile", "1");
    const user = userEvent.setup();
    renderApp("/onboarding");
    const finish = screen.getByRole("button", { name: /See my recommendations/ });
    expect(finish).toBeEnabled();
    expect(await screen.findByRole("button", { name: /Watched and disliked/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Haven’t watched/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add to watchlist/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Watched and liked/ }));
    expect(await screen.findByText("1 choices saved")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Undo latest" })).toBeEnabled();
  });

  it("filters search results with accessible controls", async () => {
    localStorage.setItem("cinematch:profiles", JSON.stringify([profile]));
    const user = userEvent.setup();
    renderApp("/search");
    const search = screen.getByLabelText("Search by title");
    await user.type(search, "Arrival");
    expect(await screen.findByRole("heading", { name: "Arrival" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Parasite" })).not.toBeInTheDocument();
  });

  it("shows an honest empty evaluation state before benchmarks run", async () => {
    renderApp("/evaluation");
    expect(await screen.findByText("No benchmark has been generated yet.")).toBeInTheDocument();
  });
});
