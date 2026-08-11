import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { CheckResultCard } from './CheckResultCard';
import type { CheckResult } from '../types';

const result = (overrides: Partial<CheckResult> = {}): CheckResult => ({
  verdict: 'FAKE',
  confidence: 20,
  model_used: 'sapling',
  explanation: 'Пояснение провайдера',
  processing_ms: 120,
  media_type: 'text',
  ...overrides,
});

describe('CheckResultCard short report', () => {
  it('renders the short report block for a new result', () => {
    const report = 'В тексте обнаружены признаки AI-генерации. Результат вероятностный.';
    const markup = renderToStaticMarkup(<CheckResultCard result={result({ short_report: report })} />);

    expect(markup).toContain('Краткий отчёт');
    expect(markup).toContain(report);
  });

  it('keeps a legacy result without short_report renderable', () => {
    const markup = renderToStaticMarkup(<CheckResultCard result={result()} />);

    expect(markup).not.toContain('Краткий отчёт');
    expect(markup).toContain('Пояснение провайдера');
  });

  it('keeps the short report in the printable result DOM', () => {
    const report = 'Первое предложение. Второе предложение.';
    const markup = renderToStaticMarkup(<CheckResultCard result={result({ short_report: report })} />);

    expect(markup).toContain(report);
    expect(markup).toContain('Экспортировать PDF');
  });
});
