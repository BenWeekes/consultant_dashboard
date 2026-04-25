import { expect, test } from "@playwright/test";

const authCookie = process.env.PLAYWRIGHT_CLIENT_AUTH_COOKIE || "";

test.skip(!authCookie, "PLAYWRIGHT_CLIENT_AUTH_COOKIE is required for authenticated AI session checks");

test.use({
  permissions: ["camera", "microphone"],
  launchOptions: {
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      "--autoplay-policy=no-user-gesture-required",
    ],
  },
  viewport: { width: 1440, height: 1100 },
});

test("authenticated AI session shows biomarkers tab", async ({ browser, baseURL }) => {
  const context = await browser.newContext({
    baseURL,
    permissions: ["camera", "microphone"],
    ignoreHTTPSErrors: true,
    viewport: { width: 1440, height: 1100 },
  });

  await context.addCookies([
    {
      name: "mindfix_client_auth",
      value: authCookie,
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

  await page.goto(
    `${baseURL}/app?profile=therapy&autoconnect=true&returnurl=${encodeURIComponent(`${baseURL}/`)}`,
    { waitUntil: "domcontentloaded" },
  );

  const biomarkersTab = page.getByRole("tab", { name: "Biomarkers" }).first();
  await expect(biomarkersTab).toBeVisible({ timeout: 30_000 });
  await biomarkersTab.click();
  await expect(page.locator("h3:visible", { hasText: "Voice Biomarkers" }).first()).toBeVisible({ timeout: 20_000 });

  await page.screenshot({
    path: "test-results/ai-biomarkers.png",
    fullPage: true,
  });

  expect(pageErrors).toEqual([]);
  await context.close();
});

test("authenticated AI session shows biomarkers tab on mobile", async ({ browser, baseURL }) => {
  const context = await browser.newContext({
    baseURL,
    permissions: ["camera", "microphone"],
    ignoreHTTPSErrors: true,
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: 3,
  });

  await context.addCookies([
    {
      name: "mindfix_client_auth",
      value: authCookie,
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

  await page.goto(
    `${baseURL}/app?profile=therapy&autoconnect=true&returnurl=${encodeURIComponent(`${baseURL}/`)}`,
    { waitUntil: "domcontentloaded" },
  );

  const biomarkersTab = page.getByRole("tab", { name: "Biomarkers" }).first();
  await expect(biomarkersTab).toBeVisible({ timeout: 30_000 });
  await biomarkersTab.click();
  await expect(page.locator("h3:visible", { hasText: "Voice Biomarkers" }).first()).toBeVisible({ timeout: 20_000 });

  await page.screenshot({
    path: "test-results/ai-biomarkers-mobile.png",
    fullPage: true,
  });

  expect(pageErrors).toEqual([]);
  await context.close();
});
