import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import { CheckResultCard, printResult } from './CheckResultCard';
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
  it('renders the completed expanded Complex report with localized evidence and confidence', () => {
    const markup = renderToStaticMarkup(<CheckResultCard result={result({
      analysis_mode: 'complex', verdict: 'REAL', confidence: 0.96, authenticity_index: 91,
      short_report: 'Детерминированный итог.',
      ai_details: {
        signals: [{ type: 'STRUCTURAL_UNIFORMITY', severity: 'HIGH', title: 'Ровная структура', explanation: 'Абзацы построены одинаково.' }],
        human_signals: ['Есть индивидуальные речевые обороты.'],
      },
      credibility: {
        status: 'completed', credibility_index: 74, verdict: 'MOSTLY_CREDIBLE', confidence: 0.8,
        model: 'gemini_credibility', processing_ms: 0, summary: 'Большинство утверждений выглядит обоснованно.',
        issues: [{ type: 'UNSUPPORTED_CLAIM', severity: 'MEDIUM', claim: 'Требуется подтверждение', explanation: 'Недостаточно оснований.', source_refs: [] }],
        credible_points: ['Даты согласованы с контекстом.'], sources: [],
      },
    })} />);

    expect(markup).toContain('Итог');
    expect(markup).toContain('Уверенность модели: 96%');
    expect(markup).toContain('Достоверность: 80%');
    expect(markup).toContain('03 · Достоверность');
    expect(markup).toContain('03 · Уверенность модели');
    expect(markup).toContain('Высокая значимость');
    expect(markup).toContain('Средняя значимость');
    expect(markup).toContain('Что говорит в пользу человеческого авторства');
    expect(markup).toContain('Что выглядит правдоподобно');
    expect(markup).toContain('Gemini Credibility');
    expect(markup).toContain('>0 мс<');
    expect(markup).toContain('complex-print-section');
    expect(markup).not.toContain('>HIGH<');
    expect(markup).not.toContain('>MEDIUM<');
    expect(markup).not.toContain('Источники');
    expect(markup.match(/Пояснение провайдера/g)).toHaveLength(1);
  });

  it('hides empty optional Complex evidence sections and preserves zero scores', () => {
    const markup = renderToStaticMarkup(<CheckResultCard result={result({
      analysis_mode: 'complex', confidence: 0, authenticity_index: 0,
      ai_details: { signals: [], human_signals: [] },
      credibility: { status: 'completed', credibility_index: 0, verdict: 'VERY_LOW_CREDIBILITY', confidence: 0, summary: 'Нет подтверждений.', issues: [], credible_points: [], sources: [] },
    })} />);

    expect(markup).toContain('>0<span');
    expect(markup).toContain('Уверенность модели: 0%');
    expect(markup).not.toContain('Что говорит в пользу человеческого авторства');
    expect(markup).not.toContain('Что выглядит правдоподобно');
    expect(markup).not.toContain('Источники');
  });

  it('calls browser print from the export action helper', () => {
    const print = vi.fn();
    vi.stubGlobal('window', { print });
    printResult();
    expect(print).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });

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

  it('renders a compact credibility block with direct index and safe source link', () => {
    const markup = renderToStaticMarkup(<CheckResultCard result={result({
      credibility: {
        status: 'completed', credibility_index: 34, verdict: 'LOW_CREDIBILITY', confidence: 0.8,
        model: 'gemini_credibility', processing_ms: 8120, summary: 'Ключевые утверждения требуют проверки.',
        issues: [{ type: 'UNSUPPORTED_CLAIM', severity: 'MEDIUM', claim: 'Сильное утверждение', explanation: 'Нет достаточного подтверждения.', source_refs: [1] }],
        sources: [{ title: 'Надёжный источник', url: 'https://example.org/source' }],
      },
    })} />);
    expect(markup).toContain('Достоверность');
    expect(markup).toContain('>34<span');
    expect(markup).toContain('Ключевые несоответствия');
    expect(markup).toContain('https://example.org/source');
    expect(markup).toContain('Проверка достоверности');
    expect(markup).toContain('Низкая достоверность');
    expect(markup).toContain('Gemini Credibility');
    expect(markup).toContain('8120 мс');
  });

  it('renders unavailable credibility without an invented score', () => {
    const markup = renderToStaticMarkup(<CheckResultCard result={result({
      credibility: { status: 'unavailable', model: 'gemini_credibility', summary: 'Проверка достоверности временно недоступна.', issues: [], sources: [] },
    })} />);
    const technicalBlock = markup.slice(markup.lastIndexOf('Проверка достоверности'));
    expect(markup).toContain('Проверка временно недоступна');
    expect(markup).toContain('Временно недоступна');
    expect(markup).toContain('Gemini Credibility');
    expect(markup).not.toContain('>0<span');
    expect(technicalBlock).not.toContain('Время обработки');
  });

  it('does not invent an authenticity index when only the AI-origin branch is unavailable', () => {
    const markup = renderToStaticMarkup(<CheckResultCard result={result({
      verdict: 'UNCERTAIN', confidence: 0.5, ai_status: 'unavailable',
      credibility: { status: 'completed', credibility_index: 81, verdict: 'HIGH_CREDIBILITY', confidence: 0.8, summary: 'Проверка доступна.', issues: [], sources: [] },
    })} />);
    expect(markup).toContain('Проверка AI-происхождения временно недоступна');
    expect(markup).not.toContain('>50<span');
    expect(markup).toContain('>81<span');
  });

  it('does not make an unsafe historical source URL clickable', () => {
    const markup = renderToStaticMarkup(<CheckResultCard result={result({
      credibility: { status: 'completed', credibility_index: 81, verdict: 'HIGH_CREDIBILITY', confidence: 0.8, summary: 'Проверка доступна.', issues: [], sources: [{ title: 'Локальный адрес', url: 'http://127.0.0.1/admin' }] },
    })} />);
    expect(markup).toContain('Локальный адрес');
    expect(markup).not.toContain('href="http://127.0.0.1/admin"');
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

  it('uses достоверность only for video-result confidence while preserving image copy', () => {
    const markup = renderToStaticMarkup(<CheckResultCard result={result({
      analysis_mode: 'complex',
      complex_media: [
        { kind: 'video', origin: 'manual', ordinal: 1, status: 'completed', authenticity_index: 95, verdict: 'REAL', confidence: 0.9, model: 'gemini_video_verification' },
        { kind: 'image', origin: 'manual', ordinal: 2, status: 'completed', authenticity_index: 88, verdict: 'REAL', confidence: 0.8, model: 'sightengine' },
      ],
    })} />);

    expect(markup).toContain('Индекс подлинности: 95% · достоверность 90%');
    expect(markup).toContain('Индекс подлинности: 88% · уверенность 80%');
    expect(markup).not.toContain('Индекс подлинности: 95% · уверенность 90%');
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
