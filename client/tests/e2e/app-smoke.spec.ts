import { expect, test } from "@playwright/test";

test("public home loads without script errors", async ({ page, baseURL }) => {
  const scriptFailures: string[] = [];
  const pageErrors: string[] = [];

  page.on("pageerror", (error) => {
    pageErrors.push(error.message);
  });
  page.on("response", (response) => {
    const request = response.request();
    if (
      ["script", "stylesheet"].includes(request.resourceType()) &&
      response.status() >= 400
    ) {
      scriptFailures.push(`${response.status()} ${response.url()}`);
    }
  });

  await page.goto(`${baseURL}/`, { waitUntil: "networkidle" });

  await expect(page.locator("body")).toContainText(/MindFix/i);
  expect(pageErrors).toEqual([]);
  expect(scriptFailures).toEqual([]);
});

test("app route does not serve broken chunks or throw runtime errors before auth", async ({ page, baseURL }) => {
  const scriptFailures: string[] = [];
  const pageErrors: string[] = [];

  page.on("pageerror", (error) => {
    pageErrors.push(error.message);
  });
  page.on("response", (response) => {
    const request = response.request();
    if (
      ["script", "stylesheet"].includes(request.resourceType()) &&
      response.status() >= 400
    ) {
      scriptFailures.push(`${response.status()} ${response.url()}`);
    }
  });

  await page.goto(
    `${baseURL}/app?profile=therapy&returnurl=${encodeURIComponent(`${baseURL}/`)}`,
    { waitUntil: "networkidle" },
  );

  await expect(page).toHaveURL(/\/(app|auth\/login|v\/[^/]+\/auth\/login)/);
  expect(pageErrors).toEqual([]);
  expect(scriptFailures).toEqual([]);
});
