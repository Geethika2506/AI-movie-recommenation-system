import { expect, test } from "@playwright/test";

test("profile feedback changes recommendations", async ({ page }, testInfo) => {
  const unique = `${testInfo.project.name}-${Date.now()}`.replace(/[^a-zA-Z0-9]/g, "");
  await page.goto("/");
  await page.getByRole("button", { name: "Need an account? Register" }).click();
  await page.getByLabel("Username").fill(`avery${unique}`);
  await page.getByLabel("Profile name").fill(`Avery ${unique}`);
  await page.getByLabel("Email").fill(`avery-${unique}@example.com`);
  await page.getByLabel("Password").fill("correct-horse-battery");
  await page.getByRole("button", { name: "Register" }).click();

  await page.getByRole("link", { name: "Discover" }).click();
  await expect(page.getByRole("heading", { name: "What do you enjoy?" })).toBeVisible();
  await page.getByRole("button", { name: "Like →" }).click();
  await expect(page.getByText("1 choices saved")).toBeVisible();
  await page.getByRole("button", { name: /Skip/ }).click();
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
