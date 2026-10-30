import { expect, test, type Page, type Route } from '@playwright/test';
import { join } from 'node:path';

const status = { admission: { running: 1, pending: 2, max_running: 2, max_pending: 8 } };
const configPath = 'admission.max_pending';

const pageCases = [
  { path: '/admin/', title: 'Overview', heading: 'Overview', evidence: 'Synthetic archive publication failed' },
  { path: '/admin/configuration', title: 'Configuration', heading: 'Configuration', evidence: 'admission.max_pending' },
  { path: '/admin/jobs', title: 'Jobs', heading: 'Jobs', evidence: '4815' },
  { path: '/admin/jobs/4815', title: 'Ticket history 4815', heading: 'Ticket history', evidence: 'Synthetic archive publication failed' },
  { path: '/admin/configuration/revisions', title: 'Revisions', heading: 'Revisions', evidence: 'admission.max_pending' },
  { path: '/admin/login', title: 'Sign in', heading: 'Sign in', evidence: 'Admin access token' },
  { path: '/de/admin/', title: 'Übersicht', heading: 'Übersicht', evidence: 'Synthetic archive publication failed' },
  { path: '/de/admin/configuration', title: 'Konfiguration', heading: 'Konfiguration', evidence: 'admission.max_pending' },
  { path: '/de/admin/jobs', title: 'Aufträge', heading: 'Aufträge', evidence: '4815' },
  { path: '/de/admin/jobs/4815', title: 'Ticketverlauf 4815', heading: 'Ticketverlauf', evidence: 'Synthetic archive publication failed' },
  { path: '/de/admin/configuration/revisions', title: 'Revisionen', heading: 'Revisionen', evidence: 'admission.max_pending' },
  { path: '/de/admin/login', title: 'Anmelden', heading: 'Anmelden', evidence: 'Admin-Zugriffstoken' },
] as const;

function observeErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  return errors;
}

async function visibility(page: Page, state: 'hidden' | 'visible'): Promise<void> {
  await page.evaluate((value) => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value });
    document.dispatchEvent(new Event('visibilitychange'));
  }, state);
}

async function noDocumentOverflow(page: Page): Promise<boolean> {
  return page.evaluate(() => document.documentElement.scrollWidth
    <= document.documentElement.clientWidth);
}

async function screenshot(page: Page, name: string): Promise<void> {
  await expect(page.locator('main')).toBeVisible();
  expect(await noDocumentOverflow(page)).toBe(true);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
  await page.screenshot({ path: join('/tmp', `chronikwerk-ui-${name}.png`), fullPage: false });
}

function validationResponse(values: Record<string, unknown>) {
  return {
    valid: true,
    overlay: { admission: { max_pending: values[configPath] } },
    diff: [{ path: configPath, before: 12, after: values[configPath] }],
    revision: 'fixture-current-revision',
  };
}

async function fulfilValidation(route: Route): Promise<void> {
  const payload = route.request().postDataJSON() as { values: Record<string, unknown> };
  await route.fulfill({ json: validationResponse(payload.values) });
}

for (const pageCase of pageCases) {
  test(`${pageCase.path} is localized, populated, and fits the viewport`, async ({ page }, info) => {
    const errors = observeErrors(page);
    await page.goto(pageCase.path);
    await expect(page).toHaveTitle(`${pageCase.title} · Chronikwerk`);
    await expect(page.getByRole('heading', { level: 1, name: pageCase.heading })).toBeVisible();
    await expect(page.locator('main')).toContainText(pageCase.evidence);
    expect((await page.locator('main').innerText()).trim().length).toBeGreaterThan(40);
    expect(await noDocumentOverflow(page)).toBe(true);
    const slug = pageCase.path.replaceAll('/', '-').replace(/^-|-$/g, '') || 'overview';
    await screenshot(page, `${slug}-${info.project.name}`);
    expect(errors).toEqual([]);
  });
}

test('the static demo keeps review invalidation, reset, and acknowledgement behavior', async ({ page }, info) => {
  const errors = observeErrors(page);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/configuration.html');
  await expect(page).toHaveTitle(/Configuration.*Chronikwerk/);
  const review = page.locator('[data-config-review]');
  const stage = page.locator('[data-config-stage]');
  const value = page.locator('#max-pending');
  const acknowledgement = page.locator('#config-ack');
  const reset = page.locator('[data-config-reset]');
  await expect(review).toBeDisabled();
  await value.fill('9');
  await expect(value.locator('xpath=..')).toHaveAttribute('data-changed', 'true');
  await expect(reset).toBeEnabled();
  await acknowledgement.check();
  await review.focus();
  await page.keyboard.press('Enter');
  await expect(stage).toBeEnabled();
  const diff = page.locator('[data-config-diff] tr');
  await expect(diff).toContainText('9');
  await expect(diff.locator('td').nth(1)).toHaveAttribute('data-label', 'Before');
  await expect(diff.locator('td').nth(2)).toHaveAttribute('data-label', 'After');
  await value.fill('10');
  await expect(stage).toBeDisabled();
  await expect(page.locator('[data-config-result]')).toContainText('Review the current changes');
  await reset.click();
  await expect(value).toHaveValue('8');
  await expect(acknowledgement).not.toBeChecked();
  await expect(page.locator('[data-config-review-panel]')).toBeHidden();
  await value.fill('9');
  await acknowledgement.check();
  await review.click();
  await stage.click();
  await expect(page.locator('[data-config-staged]')).toContainText('Synthetic revision staged');
  await screenshot(page, `demo-configuration-${info.project.name}`);
  expect(errors).toEqual([]);
});

test('configuration edits review, reset, and stage actual production fields', async ({ page }, info) => {
  const errors = observeErrors(page);
  const validations: Array<Record<string, unknown>> = [];
  await page.route('**/admin/api/v1/config/validate', async (route) => {
    const payload = route.request().postDataJSON() as {
      values: Record<string, unknown>;
      security_acknowledged: boolean;
    };
    validations.push(payload.values);
    expect(payload.security_acknowledged).toBe(false);
    await route.fulfill({ json: validationResponse(payload.values) });
  });
  await page.goto('/admin/configuration');
  await expect(page).toHaveTitle('Configuration · Chronikwerk');

  const form = page.locator('[data-config-form]');
  const row = form.locator(`[data-path="${configPath}"]`);
  const value = row.locator('input, select');
  const environmentRow = form.locator('[data-path="zammad.timeout_seconds"]');
  const environmentValue = environmentRow.locator('input, select');
  const original = await value.inputValue();
  const environmentOriginal = await environmentValue.inputValue();
  const reviewButton = form.getByRole('button', { name: 'Review changes' });
  const resetButton = form.locator('[data-config-reset]');
  const review = page.locator('[data-config-review]');
  const stage = review.locator('[data-config-stage]');

  await expect(environmentValue).toBeDisabled();
  await value.fill('17');
  await expect(row).toHaveAttribute('data-changed', 'true');
  await expect(resetButton).toBeEnabled();
  await resetButton.click();
  await expect(value).toHaveValue(original);
  await expect(row).toHaveAttribute('data-changed', 'false');
  await expect(environmentValue).toHaveValue(environmentOriginal);
  await expect(environmentValue).toBeDisabled();

  await value.fill('17');
  await reviewButton.focus();
  await page.keyboard.press('Enter');
  await expect(review).toBeVisible();
  const diffRow = review.locator(`[data-path="${configPath}"]`);
  await expect(diffRow).toContainText('17');
  await expect(diffRow.locator('td').nth(1)).toHaveAttribute('data-label', 'Before');
  await expect(diffRow.locator('td').nth(2)).toHaveAttribute('data-label', 'After');
  await expect(stage).toBeEnabled();
  await stage.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('[data-config-result]')).toContainText('fixture-staged-revision');
  await expect(stage).toBeDisabled();
  expect(validations).toEqual([{ [configPath]: 17 }]);
  await screenshot(page, `configuration-workflow-${info.project.name}`);
  expect(errors).toEqual([]);
});

test('a subsequent edit discards a delayed validation response', async ({ page }) => {
  const errors = observeErrors(page);
  let requests = 0;
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/admin/api/v1/config/validate', async (route) => {
    requests += 1;
    if (requests === 1) await delayed;
    await fulfilValidation(route);
  });
  await page.goto('/admin/configuration');
  const form = page.locator('[data-config-form]');
  const value = form.locator(`[data-path="${configPath}"] input`);
  const reviewButton = form.getByRole('button', { name: 'Review changes' });
  const review = page.locator('[data-config-review]');

  await value.fill('18');
  await reviewButton.click();
  await value.fill('19');
  release();
  await expect(reviewButton).toBeEnabled();
  await expect(review).toBeHidden();
  await expect(page.locator('[data-config-feedback]')).not.toContainText('18');
  await value.blur();
  await form.evaluate((node: HTMLFormElement) => node.requestSubmit());
  await expect.poll(() => requests).toBe(2);
  await expect(review).toBeVisible();
  await expect(review).toContainText('19');
  expect(requests).toBe(2);
  expect(errors).toEqual([]);
});

test('a delayed stage cannot be submitted twice or reuse the review', async ({ page }) => {
  const errors = observeErrors(page);
  let stageRequests = 0;
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/admin/api/v1/config/staged', async (route) => {
    stageRequests += 1;
    await delayed;
    await route.fulfill({ json: {
      revision: 'fixture-delayed-stage',
      previous_revision: 'fixture-current-revision',
      restart_required: true,
    } });
  });
  await page.goto('/admin/configuration');
  const form = page.locator('[data-config-form]');
  await form.locator(`[data-path="${configPath}"] input`).fill('20');
  await form.getByRole('button', { name: 'Review changes' }).click();
  const review = page.locator('[data-config-review]');
  const stage = review.locator('[data-config-stage]');
  await expect(stage).toBeEnabled();
  await stage.click();
  await stage.dispatchEvent('click');
  await expect(stage).toBeDisabled();
  await expect(stage).toHaveAttribute('aria-busy', 'true');
  await expect(review).toBeVisible();
  expect(stageRequests).toBe(1);
  release();
  await expect(page.locator('[data-config-result]')).toContainText('fixture-delayed-stage');
  await expect(stage).toBeDisabled();
  await expect(form.locator(`.config-field[data-path="${configPath}"]`))
    .toHaveAttribute('data-changed', 'false');
  await stage.dispatchEvent('click');
  expect(stageRequests).toBe(1);
  expect(errors).toEqual([]);
});

test('security acknowledgement is required only for security changes', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error' && !/status of 422/.test(message.text())) {
      errors.push(message.text());
    }
  });
  await page.goto('/admin/configuration');
  const form = page.locator('[data-config-form]');
  const securityValue = form.locator('[data-path="hardening.transport.trust_env"] select');
  const acknowledgement = form.locator('input[name="security_acknowledged"]');
  const reviewButton = form.getByRole('button', { name: 'Review changes' });
  await securityValue.selectOption('true');
  await reviewButton.click();
  await expect(form.locator('[data-config-errors]')).toContainText('Acknowledge the security effect');
  await expect(page.locator('[data-config-review]')).toBeHidden();
  await acknowledgement.check();
  await reviewButton.click();
  await expect(page.locator('[data-config-review]')).toBeVisible();
  await expect(page.locator('[data-config-stage]')).toBeEnabled();
  expect(errors).toEqual([]);
});

test('keyboard users can enter the app, use navigation, and submit filters', async ({ page }) => {
  const errors = observeErrors(page);
  await page.goto('/admin/');
  await page.keyboard.press('Tab');
  await expect(page.locator('.skip-link')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#main-content')).toBeFocused();

  const jobsLink = page.getByRole('link', { name: /Jobs/ });
  await jobsLink.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/admin\/jobs$/);
  const ticketFilter = page.locator('#ticket-id');
  await ticketFilter.fill('4815');
  await ticketFilter.press('Enter');
  await expect(page).toHaveURL(/ticket_id=4815/);
  expect(errors).toEqual([]);
});

test('login and retry use native required-field safeguards', async ({ page }) => {
  const errors = observeErrors(page);
  await page.goto('/admin/login');
  const token = page.locator('#access-token');
  await token.focus();
  await page.keyboard.press('Enter');
  await expect(token).toBeFocused();
  expect(await token.evaluate((input: HTMLInputElement) => input.matches(':invalid'))).toBe(true);

  await page.goto('/admin/jobs/4815');
  const retry = page.locator('details').filter({ hasText: 'Reprocess ticket' });
  await retry.locator('summary').focus();
  await page.keyboard.press('Enter');
  await expect(retry).toHaveAttribute('open', '');
  const acknowledgement = retry.locator('input[name="acknowledge_overwrite"]');
  await retry.getByRole('button', { name: 'Request reprocessing' }).click();
  expect(await acknowledgement.evaluate((input: HTMLInputElement) => input.matches(':invalid')))
    .toBe(true);
  await expect(page).toHaveURL(/\/admin\/jobs\/4815$/);
  expect(errors).toEqual([]);
});

test('restoring a revision uses disclosure and required acknowledgement', async ({ page }) => {
  const errors = observeErrors(page);
  await page.goto('/admin/configuration/revisions');
  const restore = page.locator('details').filter({ has: page.locator('form[action*="/restore"]') }).first();
  await restore.locator('summary').focus();
  await page.keyboard.press('Enter');
  await expect(restore).toHaveAttribute('open', '');
  const acknowledgement = restore.locator('input[name="security_acknowledged"]');
  await restore.getByRole('button', { name: 'Stage as a new revision' }).click();
  expect(await acknowledgement.evaluate((input: HTMLInputElement) => input.matches(':invalid')))
    .toBe(true);
  await expect(page).toHaveURL(/\/admin\/configuration\/revisions$/);
  expect(errors).toEqual([]);
});

test('slow status requests never overlap and returning to visible refreshes immediately', async ({ page }, info) => {
  const errors = observeErrors(page);
  let requests = 0;
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/admin/api/v1/status', async (route) => {
    requests += 1;
    if (requests === 1) await delayed;
    await route.fulfill({ json: status });
  });
  await page.clock.install();
  await page.goto('/admin/');
  await expect(page).toHaveTitle('Overview · Chronikwerk');
  await expect(page.locator('[data-admission-running]')).toHaveText('0');
  await page.clock.runFor(30_000);
  await expect.poll(() => requests).toBe(1);
  await page.clock.runFor(60_000);
  await visibility(page, 'hidden');
  await visibility(page, 'visible');
  expect(requests).toBe(1);
  release();
  await expect(page.locator('[data-admission-running]')).toHaveText('1');
  await expect(page.locator('[data-admission-pending]')).toHaveText('2');
  await visibility(page, 'hidden');
  await page.clock.runFor(60_000);
  expect(requests).toBe(1);
  await visibility(page, 'visible');
  await expect.poll(() => requests).toBe(2);
  await expect(page.locator('[data-refresh-status]')).toBeEmpty();
  await expect(page.locator('[data-last-refresh]')).toHaveAttribute('datetime', /Z$/);
  await screenshot(page, `overview-refresh-${info.project.name}`);
  expect(errors).toEqual([]);
});

test('status network errors recover and expiry opens the accessible sign-in dialog', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error' && !/net::ERR_FAILED|status of 401/.test(message.text())) {
      errors.push(message.text());
    }
  });
  let outcome: 'network' | 'expired' | 'ok' = 'network';
  await page.route('**/admin/api/v1/status', async (route) => {
    if (outcome === 'network') await route.abort('failed');
    else if (outcome === 'expired') await route.fulfill({ status: 401, json: {} });
    else await route.fulfill({ json: status });
  });
  await page.clock.install();
  await page.goto('/admin/');
  await page.clock.runFor(30_000);
  const refreshStatus = page.locator('[data-refresh-status]');
  await expect(refreshStatus).toHaveText(await refreshStatus.getAttribute('data-error') ?? '');
  await expect(page.locator('[data-admission-running]')).toHaveText('0');
  outcome = 'ok';
  await page.clock.runFor(30_000);
  await expect(refreshStatus).toBeEmpty();
  outcome = 'expired';
  await page.clock.runFor(30_000);
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.locator('#reauth-token')).toBeFocused();
  await expect(refreshStatus).toBeEmpty();
  await screenshot(page, `session-expired-${info.project.name}`);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeHidden();
  expect(errors).toEqual([]);
});
