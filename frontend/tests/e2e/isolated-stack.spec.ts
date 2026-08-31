import {test} from '@playwright/test';

test.describe.skip('isolated application stack', () => {
  test('runs after Todo 3 provisions the isolated backend', async ({page}) => {
    await page.goto('/');
  });
});
