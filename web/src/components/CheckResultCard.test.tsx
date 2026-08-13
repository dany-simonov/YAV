import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { CheckResultCard } from './CheckResultCard';
import type { CheckResult } from '../types';

const result = (overrides: Partial<CheckResult> = {}): CheckResult => ({
  verdict: 'FAKE',
  confidence: 20,
  authenticity_index: null,
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

  it('renders the canonical Gemini index and readable model name without recomputing it', () => {
    const markup = renderToStaticMarkup(<CheckResultCard result={result({
      verdict: 'FAKE',
      confidence: 0.95,
      authenticity_index: 10,
      model_used: 'gemini_video_verification',
    })} />);

    expect(markup).toContain('>10<span');
    expect(markup).not.toContain('>5<span');
    expect(markup).toContain('Gemini Video Verification');
    expect(markup).not.toContain('gemini_video_verification');
  });

  it('renders a canonical real-video index directly', () => {
    const markup = renderToStaticMarkup(<CheckResultCard result={result({
      verdict: 'REAL',
      confidence: 0.9,
      authenticity_index: 95,
    })} />);

    expect(markup).toContain('>95<span');
    expect(markup).not.toContain('>90<span');
  });

  it('renders a canonical Gemini TEXT index and readable model name directly', () => {
    const markup = renderToStaticMarkup(<CheckResultCard result={result({
      verdict: 'FAKE',
      confidence: 0.93,
      authenticity_index: 12,
      model_used: 'gemini_text_verification',
    })} />);

    expect(markup).toContain('>12<span');
    expect(markup).not.toContain('>7<span');
    expect(markup).toContain('Gemini Text Verification');
    expect(markup).not.toContain('gemini_text_verification');
  });
});
