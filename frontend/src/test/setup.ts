import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Backend unavailable")));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
