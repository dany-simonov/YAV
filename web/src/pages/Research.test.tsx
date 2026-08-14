import { renderToStaticMarkup } from 'react-dom/server';
import type { ReactNode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { researchCases } from '../data/researchCases';
import { ArcticResearch, Research, ResearchCasePage } from './Research';

const renderPage = (page: ReactNode) => renderToStaticMarkup(
  <MemoryRouter>{page}</MemoryRouter>,
);

const renderCase = (slug: string) => renderToStaticMarkup(
  <MemoryRouter initialEntries={[`/research/arctic/${slug}`]}>
    <Routes>
      <Route path="/research/arctic/:caseSlug" element={<ResearchCasePage />} />
    </Routes>
  </MemoryRouter>,
);

describe('Research pages', () => {
  it('shows the AZRF direction on the research index', () => {
    const markup = renderPage(<Research />);

    expect(markup).toContain('Исследования АЗРФ');
    expect(markup).toContain('href="/research/arctic"');
  });

  it('shows four studies inside the AZRF direction', () => {
    const markup = renderPage(<ArcticResearch />);

    expect(markup.match(/Кейс 0[1-4]/g)).toHaveLength(4);
    expect(markup).toContain('Атомные подводные газовозы');
    expect(markup).toContain('href="/research/arctic/nuclear-lng-carriers"');
    expect(markup).toContain('Была ли Восточная Сибирь ближе к экватору?');
    expect(markup).toContain('Мумия неизвестного «динозавра» из Сибири');
    expect(markup).toContain('Гигантский монстр в глубинах Арктики');
    expect(markup.match(/href="\/research\/arctic\//g)).toHaveLength(4);
  });

  it('renders the full verdict, comparison and final classification', () => {
    const markup = renderCase('nuclear-lng-carriers');

    expect(markup).toContain('href="/research/arctic"');
    expect(markup).toContain('Проверка статьи Reuters от 16 октября 2024 года');
    expect(markup).toContain('Что показала модель и что подтверждается на практике');
    expect(markup).toContain('180 000 тонн или 180 000 м³?');
    expect(markup).toContain('Статья достоверна, проект — пока нет.');
    expect(markup).toContain('Factually grounded / Highly speculative');
  });

  it.each([
    ['pleistocene-pole-shift', 'Настоящая научная статья — крайне спорная гипотеза.'],
    ['siberian-mummified-animal', 'Настоящая мумия — почти наверняка не динозавр.'],
    ['arctic-sea-monster', 'Реальные детали — вымышленное событие.'],
  ])('renders the complete %s research case', (slug, finalVerdict) => {
    const markup = renderCase(slug);

    expect(markup).toContain(finalVerdict);
    expect(markup).toContain('Источники проверки');
    expect(markup).toContain('Оценка отражает уверенность модели');
  });

  it('marks reference outputs as benchmarks in data without exposing that label in UI', () => {
    expect(researchCases[0].benchmarkModelResult).toBe(false);
    expect(researchCases.slice(1).every((item) => item.benchmarkModelResult)).toBe(true);
    expect(renderCase('pleistocene-pole-shift')).not.toContain('benchmarkModelResult');
  });
});
