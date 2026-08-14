import { ArrowLeft, ArrowRight, Map } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';

import { ResearchCaseView } from '../components/research/ResearchCaseView';
import { getResearchCase, researchCases, type ResearchTone } from '../data/researchCases';

const toneText: Record<ResearchTone, string> = {
  positive: 'text-mv-real',
  warning: 'text-mv-uncertain',
  negative: 'text-mv-fake',
  neutral: 'text-mv-text-secondary',
};

const toneDot: Record<ResearchTone, string> = {
  positive: 'bg-mv-real',
  warning: 'bg-mv-uncertain',
  negative: 'bg-mv-fake',
  neutral: 'bg-black/35',
};

export function Research() {
  return (
    <div className="pt-32 pb-24 lg:pb-32">
      <section className="container">
        <p className="eyebrow">Исследовательское направление ЯВЬ</p>
        <h1 className="section-title mt-6 max-w-3xl">Исследования цифрового контента</h1>
        <p className="mt-7 max-w-2xl text-lg leading-8 text-mv-text-secondary">Материалы о происхождении цифрового контента, проверке медиаданных и применении воспроизводимых методов анализа.</p>

        <Link to="/research/arctic" className="group mt-16 grid gap-6 border-y border-black/[.09] py-9 transition-colors hover:border-black/[.18] md:grid-cols-[180px_1fr_auto] md:items-center">
          <span className="eyebrow flex items-center gap-2"><Map size={15} /> Направление 01</span>
          <div>
            <h2 className="text-2xl font-semibold tracking-[-.035em]">Исследования АЗРФ</h2>
            <p className="mt-3 max-w-2xl leading-7 text-mv-text-secondary">Исследование информационной достоверности и детекция ИИ-контента в медиаполе Арктической зоны Российской Федерации.</p>
          </div>
          <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-black/[.1] bg-white shadow-sm transition-transform group-hover:translate-x-1"><ArrowRight size={18} /></span>
        </Link>
      </section>
    </div>
  );
}

export function ArcticResearch() {
  return (
    <div className="pt-28 pb-28 lg:pt-32 lg:pb-36">
      <section className="container">
        <Link to="/research" className="eyebrow inline-flex items-center gap-2 transition-colors hover:text-black"><ArrowLeft size={14} /> Все направления</Link>

        <header className="mt-10 grid items-end gap-10 border-b border-black/[.09] pb-12 lg:grid-cols-[1fr_300px] lg:gap-20 lg:pb-16">
          <div>
            <p className="eyebrow">Грантовый модуль ПОРА · АЗРФ</p>
            <h1 className="mt-6 max-w-5xl text-[clamp(2.6rem,5.5vw,5rem)] font-semibold leading-[.98] tracking-[-.06em]">Исследования информационной достоверности в медиаполе АЗРФ</h1>
          </div>
          <p className="border-l border-black/[.1] pl-6 text-sm leading-6 text-mv-text-secondary">Четыре исследовательских кейса: реальная концепция, спорная научная гипотеза, сенсационная интерпретация и прямо опровергнутая история.</p>
        </header>

        <div className="mt-12 grid gap-4 lg:grid-cols-2">
          {researchCases.map((study) => (
            <Link
              key={study.slug}
              to={`/research/arctic/${study.slug}`}
              className="group flex min-h-[330px] flex-col rounded-2xl border border-black/[.09] bg-white p-7 transition duration-300 hover:-translate-y-1 hover:border-black/[.16] hover:shadow-[0_18px_48px_rgba(0,0,0,.07)] sm:p-9"
            >
              <div className="flex items-start justify-between gap-5">
                <p className="eyebrow">Кейс {study.number}</p>
                <ArrowRight className="shrink-0 transition-transform group-hover:translate-x-1" size={18} />
              </div>
              <h2 className="mt-6 text-2xl font-semibold leading-tight tracking-[-.035em] sm:text-3xl">{study.cardTitle}</h2>
              <p className="mt-5 flex-1 text-sm leading-6 text-mv-text-secondary">{study.cardDescription}</p>
              <div className="mt-8 flex items-end justify-between gap-5 border-t border-black/[.08] pt-5">
                <span className={`inline-flex items-center gap-2 text-sm font-semibold ${toneText[study.tone]}`}><span className={`h-1.5 w-1.5 rounded-full ${toneDot[study.tone]}`} />{study.cardStatus}</span>
                <strong className="text-3xl tracking-[-.05em]">{study.cardScore}<span className="text-base text-mv-text-muted">%</span></strong>
              </div>
              <div className="mt-4 h-1 overflow-hidden rounded-full bg-black/[.06]"><div className="h-full rounded-full bg-black" style={{ width: `${study.cardScore}%` }} /></div>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}

export function ResearchCasePage() {
  const { caseSlug } = useParams();
  const researchCase = getResearchCase(caseSlug);

  if (!researchCase) {
    return (
      <div className="container flex min-h-[70vh] flex-col items-center justify-center py-32 text-center">
        <p className="eyebrow">Исследование не найдено</p>
        <h1 className="section-title mt-5">Такого кейса пока нет</h1>
        <Link to="/research/arctic" className="btn-black mt-8">Все исследования</Link>
      </div>
    );
  }

  return <ResearchCaseView researchCase={researchCase} />;
}
