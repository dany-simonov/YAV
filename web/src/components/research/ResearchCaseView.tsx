import {
  AlertTriangle,
  ArrowLeft,
  Check,
  ExternalLink,
  Info,
  Scale,
} from 'lucide-react';
import { Link } from 'react-router-dom';

import type {
  MetricVisual,
  ResearchCase,
  ResearchEvidence,
  ResearchMetric,
  ResearchTone,
} from '../../data/researchCases';

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

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-t border-black/[.08] py-4">
      <p className="text-[11px] font-semibold uppercase tracking-[.08em] text-mv-text-muted">{label}</p>
      <p className="mt-2 text-sm font-medium">{value}</p>
    </div>
  );
}

function ScoreBar({ value, label }: { value: number; label: string }) {
  return (
    <div aria-label={`${label}: ${value}%`}>
      <div className="mb-2 flex items-center justify-between gap-4 text-sm">
        <span className="text-mv-text-secondary">{label}</span>
        <strong>{value}%</strong>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-black/[.06]">
        <div className="h-full rounded-full bg-black transition-[width]" style={{ width: `${value}%` }} />
      </div>
    </div>
  );
}

function MetricVisualBlock({ visual }: { visual: MetricVisual }) {
  if (visual.type === 'dimensions') {
    return (
      <div className="mt-7 grid grid-cols-2 gap-3">
        {visual.values.map((item) => (
          <div key={item.label} className="rounded-xl bg-black/[.035] p-5">
            <strong className="text-3xl tracking-[-.05em]">{item.value}</strong>
            <p className="mt-2 text-xs text-mv-text-muted">{item.label}</p>
          </div>
        ))}
      </div>
    );
  }

  if (visual.type === 'duration') {
    const max = Math.max(visual.from, visual.to);
    const rows = [
      { label: visual.fromLabel, value: visual.from },
      { label: visual.toLabel, value: visual.to },
    ];

    return (
      <div className="mt-8">
        <p className="mb-6 inline-flex rounded-full border border-black/[.1] px-3 py-1.5 text-xs font-semibold">{visual.delta}</p>
        <div className="grid gap-5">
          {rows.map((row, index) => (
            <div key={row.label} className="grid grid-cols-[72px_1fr_72px] items-center gap-4">
              <span className="text-sm text-mv-text-secondary">{row.label}</span>
              <div className="h-8 overflow-hidden rounded-md bg-black/[.06]">
                <div className={index === 0 ? 'h-full rounded-md bg-black/[.16]' : 'h-full rounded-md bg-black'} style={{ width: `${(row.value / max) * 100}%` }} />
              </div>
              <strong className="text-right">{row.value} {visual.unit}</strong>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="mt-7 grid items-stretch gap-3 sm:grid-cols-[1fr_auto_1fr]">
      <div className="rounded-xl bg-black/[.035] p-5">
        <strong className="text-3xl tracking-[-.05em]">{visual.left.value}</strong>
        <p className="mt-2 text-xs text-mv-text-muted">{visual.left.label}</p>
      </div>
      <span className="flex items-center justify-center text-2xl text-mv-text-muted">vs</span>
      <div className="rounded-xl border border-black/[.08] p-5">
        <strong className="text-xl tracking-[-.035em]">{visual.right.value}</strong>
        <p className="mt-2 text-xs text-mv-text-muted">{visual.right.label}</p>
      </div>
    </div>
  );
}

function MetricCard({ metric }: { metric: ResearchMetric }) {
  const tone = metric.tone ?? 'neutral';

  return (
    <article className={`rounded-2xl border border-black/[.09] bg-white p-7 sm:p-9 ${metric.wide ? 'lg:col-span-2' : ''}`}>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="eyebrow">{metric.label}</p>
          <h3 className="mt-5 text-2xl font-semibold tracking-[-.035em]">{metric.title}</h3>
        </div>
        {metric.status && (
          <span className={`inline-flex shrink-0 items-center gap-2 self-start text-xs font-semibold ${toneText[tone]}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${toneDot[tone]}`} />{metric.status}
          </span>
        )}
      </div>

      {metric.score !== undefined && <div className="mt-7"><ScoreBar value={metric.score} label={metric.scoreLabel ?? 'Оценка модели'} /></div>}
      {metric.visual && <MetricVisualBlock visual={metric.visual} />}

      <div className="mt-7 grid gap-5 border-t border-black/[.08] pt-5 sm:grid-cols-2">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[.08em] text-mv-text-muted">{metric.claimLabel}</p>
          <p className="mt-2 text-sm leading-6">{metric.claim}</p>
        </div>
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[.08em] text-mv-text-muted">{metric.evidenceLabel}</p>
          <p className="mt-2 text-sm leading-6 text-mv-text-secondary">{metric.evidence}</p>
        </div>
      </div>

      {metric.note && <p className="mt-5 border-t border-black/[.08] pt-5 text-sm font-semibold">{metric.note}</p>}
    </article>
  );
}

function ScoreSummary({ items }: { items: NonNullable<ResearchCase['scoreSummary']> }) {
  return (
    <div className="mt-12 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {items.map((item) => (
        <div key={item.label} className="rounded-2xl border border-black/[.09] bg-white p-6 sm:p-7">
          <p className="text-xs font-semibold uppercase tracking-[.08em] text-mv-text-muted">{item.label}</p>
          <div className="mt-7 flex items-end justify-between gap-4">
            <strong className="text-5xl leading-none tracking-[-.065em]">{item.value}%</strong>
            <span className="pb-1 text-xs font-semibold text-mv-text-secondary">{item.caption}</span>
          </div>
          <div className="mt-6 h-1.5 overflow-hidden rounded-full bg-black/[.06]"><div className="h-full rounded-full bg-black" style={{ width: `${item.value}%` }} /></div>
        </div>
      ))}
    </div>
  );
}

function EvidenceBlock({ evidence }: { evidence: ResearchEvidence }) {
  if (evidence.type === 'direct-contradiction') {
    return (
      <section className="border-t border-black/[.09] py-16 lg:py-24" aria-labelledby="evidence-title">
        <p className="eyebrow">{evidence.eyebrow}</p>
        <h2 id="evidence-title" className="section-title mt-5">{evidence.title}</h2>
        <div className="mt-10 grid gap-4 lg:grid-cols-[.8fr_1.2fr]">
          <div className="rounded-2xl border border-black/[.1] bg-white p-7 sm:p-9">
            <p className="eyebrow">Публикация</p>
            <p className="mt-8 text-2xl font-semibold leading-tight tracking-[-.035em]">{evidence.claim}</p>
          </div>
          <div className="rounded-2xl bg-black p-7 text-white sm:p-9">
            <p className="text-[11px] font-semibold uppercase tracking-[.08em] text-white/45">Проверка · Прямое противоречие</p>
            <p className="mt-8 text-3xl font-semibold leading-tight tracking-[-.045em]">{evidence.correction}</p>
            <p className="mt-7 border-t border-white/15 pt-6 text-sm leading-6 text-white/65">{evidence.summary}</p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="border-t border-black/[.09] py-16 lg:py-24" aria-labelledby="evidence-title">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-1 shrink-0" size={22} />
        <div><p className="eyebrow">{evidence.eyebrow}</p><h2 id="evidence-title" className="section-title mt-5">{evidence.title}</h2></div>
      </div>

      <div className="mt-10 grid items-stretch gap-3 md:grid-cols-[1fr_auto_1fr]">
        <div className="rounded-2xl border border-black/[.1] bg-white p-7 sm:p-9">
          <p className="eyebrow">{evidence.left.label}</p>
          <p className="mt-8 text-[clamp(2rem,5vw,3rem)] font-semibold leading-none tracking-[-.065em]">{evidence.left.value}</p>
          <p className="mt-3 text-mv-text-secondary">{evidence.left.caption}</p>
        </div>
        <div className="flex items-center justify-center px-4 py-2 text-3xl font-light text-mv-text-muted">≠</div>
        <div className="rounded-2xl border border-black/[.1] bg-white p-7 sm:p-9">
          <p className="eyebrow">{evidence.right.label}</p>
          <p className="mt-8 text-[clamp(2rem,5vw,3rem)] font-semibold leading-none tracking-[-.065em]">{evidence.right.value}</p>
          <p className="mt-3 text-mv-text-secondary">{evidence.right.caption}</p>
        </div>
      </div>

      <div className="mt-4 rounded-2xl border border-black/[.09] bg-black/[.025] p-6 sm:p-8">
        <p className="max-w-4xl text-sm leading-6 text-mv-text-secondary">{evidence.summary}</p>
        {evidence.caveat && <p className="mt-3 max-w-4xl text-xs leading-5 text-mv-text-muted">{evidence.caveat}</p>}
      </div>
    </section>
  );
}

function Sources({ sources }: { sources: ResearchCase['sources'] }) {
  return (
    <section className="border-t border-black/[.09] py-16 lg:py-24" aria-labelledby="sources-title">
      <p className="eyebrow">Проверяемые материалы</p>
      <h2 id="sources-title" className="section-title mt-5">Источники проверки</h2>
      <div className="mt-10 grid gap-4 md:grid-cols-2">
        {sources.map((source) => (
          <article key={source.url} className="flex min-h-[220px] flex-col rounded-2xl border border-black/[.09] bg-white p-7 sm:p-8">
            <div className="flex flex-wrap items-center gap-2 text-[11px] font-semibold uppercase tracking-[.08em] text-mv-text-muted">
              <span>{source.organization}</span><span className="text-black/20">/</span><span>{source.type}</span>
            </div>
            <h3 className="mt-5 text-xl font-semibold leading-snug tracking-[-.025em]">{source.title}</h3>
            <a href={source.url} target="_blank" rel="noreferrer" className="btn-light mt-auto self-start">
              Открыть источник <ExternalLink size={15} />
            </a>
          </article>
        ))}
      </div>
    </section>
  );
}

export function ResearchCaseView({ researchCase }: { researchCase: ResearchCase }) {
  return (
    <div className="pt-28 pb-28 lg:pt-32 lg:pb-36">
      <article className="container">
        <Link to="/research/arctic" className="eyebrow inline-flex items-center gap-2 transition-colors hover:text-black">
          <ArrowLeft size={14} /> Все исследования
        </Link>

        <header className="mt-10 grid gap-10 border-b border-black/[.09] pb-12 lg:grid-cols-[1fr_300px] lg:gap-20 lg:pb-16">
          <div>
            <p className="eyebrow">Кейс {researchCase.number}</p>
            <h1 className="mt-6 max-w-5xl text-[clamp(2.6rem,5.5vw,5rem)] font-semibold leading-[.98] tracking-[-.06em]">{researchCase.title}</h1>
            <p className="mt-7 max-w-3xl text-lg leading-8 text-mv-text-secondary">{researchCase.subtitle}</p>
          </div>
          <aside className="self-end">{researchCase.metadata.map((item) => <MetaItem key={item.label} {...item} />)}</aside>
        </header>

        <section className="py-16 lg:py-24" aria-labelledby={`verdict-${researchCase.number}`}>
          <div className="grid gap-10 rounded-[22px] border border-black/[.09] bg-white p-7 shadow-[0_20px_60px_rgba(0,0,0,.06)] sm:p-10 lg:grid-cols-[.75fr_1.25fr] lg:gap-16 lg:p-12">
            <div>
              <p className="eyebrow">{researchCase.credibilityLabel}</p>
              <h2 id={`verdict-${researchCase.number}`} className="mt-5 text-3xl font-semibold tracking-[-.045em] sm:text-4xl">{researchCase.verdict}</h2>
              <div className="mt-10 flex items-end gap-2">
                <strong className="text-7xl font-semibold leading-none tracking-[-.075em]">{researchCase.credibilityScore}</strong>
                <span className="pb-1 text-2xl text-mv-text-muted">%</span>
              </div>
              <div className="mt-6 h-2 overflow-hidden rounded-full bg-black/[.06]"><div className="h-full rounded-full bg-black" style={{ width: `${researchCase.credibilityScore}%` }} /></div>
              <div className="mt-5 flex gap-2 text-xs leading-5 text-mv-text-muted">
                <Info className="mt-0.5 shrink-0" size={14} />
                <p>Оценка отражает уверенность модели на основе совокупности проверенных утверждений и не является статистической вероятностью.</p>
              </div>
            </div>
            <div className="border-t border-black/[.08] pt-8 lg:border-l lg:border-t-0 lg:pl-12 lg:pt-0">
              <p className="text-xl leading-9 tracking-[-.02em] text-mv-text-secondary sm:text-2xl">{researchCase.summary}</p>
            </div>
          </div>
        </section>

        <section className="border-t border-black/[.09] py-16 lg:py-24" aria-labelledby={`ai-text-${researchCase.number}`}>
          <div className="grid gap-10 lg:grid-cols-[.75fr_1.25fr] lg:gap-20">
            <div>
              <p className="eyebrow">Проверка текста</p>
              <h2 id={`ai-text-${researchCase.number}`} className="section-title mt-5">Написан ли текст ИИ?</h2>
            </div>
            <div className="rounded-2xl border border-black/[.09] bg-white p-7 sm:p-9">
              <div className="flex flex-wrap items-end justify-between gap-6">
                <div>
                  <p className="text-sm font-semibold">{researchCase.textAiCheck.verdict}</p>
                  <p className="mt-3 max-w-2xl text-sm leading-6 text-mv-text-secondary">{researchCase.textAiCheck.explanation}</p>
                </div>
                <div className="shrink-0 text-right">
                  <strong className="text-5xl leading-none tracking-[-.06em]">{researchCase.textAiCheck.score}%</strong>
                  <p className="mt-2 text-[11px] font-semibold uppercase tracking-[.08em] text-mv-text-muted">вероятность ИИ</p>
                </div>
              </div>
              <div className="mt-7 h-1.5 overflow-hidden rounded-full bg-black/[.06]">
                <div className="h-full rounded-full bg-black" style={{ width: `${researchCase.textAiCheck.score}%` }} />
              </div>
              <p className="mt-4 text-xs leading-5 text-mv-text-muted">Это оценка языковых признаков, а не способ достоверно установить автора текста.</p>
            </div>
          </div>
        </section>

        <section className="border-t border-black/[.09] py-16 lg:py-24" aria-labelledby={`comparison-${researchCase.number}`}>
          <p className="eyebrow">Главное сравнение</p>
          <h2 id={`comparison-${researchCase.number}`} className="section-title mt-5 max-w-4xl">{researchCase.comparisonTitle}</h2>
          {researchCase.scoreSummary && <ScoreSummary items={researchCase.scoreSummary} />}
          <div className={`${researchCase.scoreSummary ? 'mt-4' : 'mt-12'} grid gap-4 lg:grid-cols-2`}>
            {researchCase.metrics.map((metric) => <MetricCard key={metric.label} metric={metric} />)}
          </div>
        </section>

        <EvidenceBlock evidence={researchCase.evidence} />

        <section className="grid gap-12 border-t border-black/[.09] py-16 lg:grid-cols-[.75fr_1.25fr] lg:gap-20 lg:py-24">
          <div><p className="eyebrow">Ключевые несоответствия</p><h2 className="section-title mt-5">{researchCase.discrepanciesTitle}</h2></div>
          <div className="border-t border-black/[.09]">
            {researchCase.discrepancies.map((item, index) => (
              <div key={item.title} className="grid gap-3 border-b border-black/[.09] py-6 sm:grid-cols-[48px_1fr] sm:py-8">
                <span className="text-sm font-semibold text-mv-text-muted">{String(index + 1).padStart(2, '0')}</span>
                <div><h3 className="text-lg font-semibold tracking-[-.02em]">{item.title}</h3><p className="mt-3 text-sm leading-6 text-mv-text-secondary">{item.text}</p></div>
              </div>
            ))}
          </div>
        </section>

        <section className="grid gap-12 border-t border-black/[.09] py-16 lg:grid-cols-[.75fr_1.25fr] lg:gap-20 lg:py-24">
          <div><p className="eyebrow">Подтверждено</p><h2 className="section-title mt-5">Что модель определила правильно</h2></div>
          <div className="border-t border-black/[.09]">
            {researchCase.confirmedFacts.map((item) => (
              <div key={item} className="flex gap-4 border-b border-black/[.09] py-5 text-sm leading-6 sm:py-6">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-mv-real/30 text-mv-real"><Check size={12} /></span>
                <span>{item}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-[24px] bg-black p-8 text-white sm:p-11 lg:p-14">
          <p className="eyebrow !text-white/45">Итог</p>
          <h2 className="mt-6 max-w-4xl text-4xl font-semibold leading-[1.04] tracking-[-.055em] sm:text-5xl lg:text-6xl">{researchCase.finalVerdict}</h2>
          <p className="mt-8 max-w-3xl text-base leading-7 text-white/65 sm:text-lg">{researchCase.finalText}</p>
          <div className="mt-10 grid grid-cols-1 gap-5 border-t border-white/15 pt-8 sm:grid-cols-2 lg:grid-cols-4">
            <div><p className="text-xs uppercase tracking-[.08em] text-white/40">Классификация</p><p className="mt-3 font-semibold">{researchCase.classification}</p></div>
            {researchCase.finalScores.map((item) => <div key={item.label}><p className="text-xs uppercase tracking-[.08em] text-white/40">{item.label}</p><p className="mt-3 text-2xl font-semibold">{item.value}</p></div>)}
          </div>
        </section>

        <Sources sources={researchCase.sources} />

        <div className="flex items-center gap-2 border-t border-black/[.09] pt-7 text-sm text-mv-text-secondary">
          <Scale size={16} /> Исследование {researchCase.number} · ЯВЬ
        </div>
      </article>
    </div>
  );
}
