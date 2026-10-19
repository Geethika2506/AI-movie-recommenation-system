import { expect, test } from "@playwright/test";

test("profile feedback changes recommendations", async ({ page }, testInfo) => {
  await page.goto("/profiles");
  await page.getByPlaceholder("Profile name").fill(`Avery ${testInfo.project.name} ${Date.now()}`);
  await page.getByRole("button", { name: "Create profile" }).click();

  await expect(page.getByRole("heading", { name: "What have you watched?" })).toBeVisible();
  await page.getByRole("button", { name: /Watched and liked/ }).click();
  await expect(page.getByText("1 choices saved")).toBeVisible();
  await page.getByRole("button", { name: /Haven’t watched/ }).click();
  await expect(page.getByText("2 choices saved")).toBeVisible();
  await page.getByRole("button", { name: "Undo latest" }).click();
  await expect(page.getByText("1 choices saved")).toBeVisible();
  await page.getByRole("button", { name: /See my recommendations/ }).click();

  await expect(page.getByRole("heading", { name: "Your next favorite is waiting" })).toBeVisible();
  const firstTitle = await page.locator(".movie-card h3").first().innerText();
  await page.getByLabel(`Mark ${firstTitle} watched`).click();
  await expect(page.getByRole("status")).toContainText("Marked as watched");
  await page.reload();
  await expect(page.getByRole("heading", { name: firstTitle, exact: true })).toHaveCount(0);

  await page.getByRole("link", { name: "Search" }).click();
  await page.getByLabel("Search by title").fill("Toy Story");
  await page.getByRole("link", { name: "Toy Story", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "Toy Story", exact: true })).toBeVisible();
});
