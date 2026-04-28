import { expect, test } from "@playwright/test";

const consultantCookie = process.env.PLAYWRIGHT_CONSULTANT_SESSION_COOKIE || "";
const expectedMeetingTitle = process.env.PLAYWRIGHT_CONSULTANT_UPCOMING_EXPECTED || "";
const vendorSlug = process.env.PLAYWRIGHT_CONSULTANT_VENDOR_SLUG || "mindfix";

test.skip(!consultantCookie, "PLAYWRIGHT_CONSULTANT_SESSION_COOKIE is required for consultant upcoming checks");

test("consultant upcoming meetings page renders", async ({ browser, baseURL }) => {
  const context = await browser.newContext({
    baseURL,
    ignoreHTTPSErrors: true,
    viewport: { width: 1440, height: 1100 },
  });

  await context.addCookies([
    {
      name: "session",
      value: consultantCookie,
      domain: "mindfix.me",
      path: "/",
      httpOnly: true,
      secure: true,
      sameSite: "Lax",
    },
  ]);

  const page = await context.newPage();
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.goto(`${baseURL}/v/${vendorSlug}/consultant/meetings?filter=upcoming`, {
    waitUntil: "networkidle",
  });

  await expect(page).toHaveURL(new RegExp(`/v/${vendorSlug}/consultant/meetings\\?filter=upcoming`));
  await expect(page.getByRole("heading", { name: "Meetings" })).toBeVisible({ timeout: 30_000 });

  if (expectedMeetingTitle) {
    await expect(page.getByText(expectedMeetingTitle).first()).toBeVisible({ timeout: 30_000 });
  } else {
    await expect(page.locator("body")).toContainText(/No meetings found|Meetings/i);
  }

  await page.screenshot({
    path: "test-results/consultant-upcoming.png",
    fullPage: true,
  });

  expect(pageErrors).toEqual([]);
  await context.close();
});
