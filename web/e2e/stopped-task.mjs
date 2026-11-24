// Run after building web/. All requests, including app assets, are fulfilled locally.
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const browser = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : { channel: 'msedge' });
try {
  const page = await browser.newPage();
  const user = { id: 'fixture', role: 'student', username: 'fixture' };
  await page.addInitScript(user => sessionStorage.setItem('clinic.identity', JSON.stringify({ mode: 'local', user })), user);
  const starts = [];
  const tasks = ['not_started', 'completed', 'in_progress', 'completed'].map((status, i) => ({
    id: `t${i}`, title: `Task ${i}`, status, open_session_id: i >= 2 ? `open${i}` : null,
    latest_sealed_session_id: status === 'completed' ? `sealed${i}` : null,
    case_public_id: 'CASE-T100', case: { available: false, unavailable_reason: 'stopped' },
  }));
  await page.route('**/*', route => {
    const { pathname } = new URL(route.request().url());
    if (['/', '/assets/main.css', '/assets/main.js'].includes(pathname))
      return route.fulfill({ path: fileURLToPath(new URL('../dist' + (pathname === '/' ? '/index.html' : pathname), import.meta.url)) });
    if (pathname === '/api/auth/me') return route.fulfill({ json: user });
    if (pathname === '/health') return route.fulfill({ json: { patient_adapter: 'simulator' } });
    if (pathname === '/api/assignments') return route.fulfill({ json: tasks });
    if (pathname.endsWith('/start')) {
      starts.push([route.request().method(), pathname]);
      return route.fulfill({ json: { session_id: 'open2', resumed: true } });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto('http://127.0.0.1/#/tasks');
  await page.getByRole('row').filter({ hasText: 'Task 3' }).waitFor();
  await page.getByRole('button', { name: /^全部/ }).click();
  const button = i => page.getByRole('row').filter({ hasText: `Task ${i}` }).getByRole('button', { name: /^(开始|继续|再练一次)$/ });
  assert.ok(await button(0).isDisabled() && await button(1).isDisabled(), 'new and repeat starts stay blocked');
  for (const i of [2, 3]) {
    assert.equal(await button(i).innerText(), '继续');
    assert.ok(await button(i).isEnabled(), `resume existing session for Task ${i}`);
  }
  await button(2).click();
  await page.waitForURL('**/#/training/open2');
  assert.deepEqual(starts, [['POST', '/api/assignments/t2/start']]);
  await page.goto('http://127.0.0.1/#/tasks');
  await page.getByRole('row').filter({ hasText: 'Task 3' }).getByRole('button', { name: '查看复盘' }).click();
  await page.waitForURL('**/#/training/sealed3');
  console.log('Stopped tasks: new starts blocked; existing sessions resumable, including repeat attempts.');
} finally { await browser.close(); }
