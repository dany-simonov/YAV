import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { Home } from './Home';

describe('Home hero actions', () => {
  it('uses working application navigation instead of preview-only links', () => {
    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <Home />
      </MemoryRouter>,
    );

    expect(markup).toContain('href="/dashboard/check"');
    expect(markup).toContain('aria-controls="report"');
    expect(markup).toContain('Посмотреть пример отчёта');
    expect(markup).not.toContain('od://');
  });
});
