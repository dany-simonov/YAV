import { Download } from 'lucide-react';

import type { Check, CheckResult, Severity } from '../types';
import { displayAuthenticityIndex, displayModelName } from '../lib/resultPresentation';

interface Props { result: CheckResult | Check }

const labels = { REAL: 'Признаки генерации не выражены', FAKE: 'Найдены признаки AI-генерации', UNCERTAIN: 'Требуется дополнительная проверка' };
const status = { REAL: 'text-mv-real', FAKE: 'text-mv-fake', UNCERTAIN: 'text-mv-uncertain' };
const credibilityLabels = { VERY_LOW_CREDIBILITY: 'Крайне низкая достоверность', LOW_CREDIBILITY: 'Низкая достоверность', MIXED_CREDIBILITY: 'Спорная достоверность', MOSTLY_CREDIBLE: 'Преимущественно достоверный материал', HIGH_CREDIBILITY: 'Высокая достоверность' };
const severityLabels: Record<Severity, string> = { HIGH: 'Высокая значимость', MEDIUM: 'Средняя значимость', LOW: 'Низкая значимость' };

const safeUrl = (value: string) => {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/\.$/, '');
    const privateV4 = /^(127|10)\.|^192\.168\.|^172\.(1[6-9]|2\d|3[01])\./.test(host);
    return (url.protocol === 'https:' || url.protocol === 'http:') && host !== 'localhost' && !host.endsWith('.localhost') && !privateV4 ? url.href : null;
  } catch { return null; }
};

const validProcessingMs = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 60_000;

const confidenceLabel = (value: unknown): string | null => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1) return null;
  return `Уверенность модели: ${Math.round(value * 100)}%`;
};

export const printResult = () => window.print();

export function CheckResultCard({ result }: Props) {
  const isComplex = result.analysis_mode === 'complex';
  const aiUnavailable = result.ai_status === 'unavailable';
  const authenticityIndex = aiUnavailable ? null : displayAuthenticityIndex(result.authenticity_index, result.confidence, result.verdict);
  const modelName = displayModelName(result.model_used);
  const credibility = result.credibility;
  const credibilityIndex = credibility?.status === 'completed' && typeof credibility.credibility_index === 'number' ? credibility.credibility_index : null;
  const aiLabel = aiUnavailable ? 'Проверка AI-происхождения временно недоступна' : labels[result.verdict];
  const aiConfidence = isComplex && !aiUnavailable ? confidenceLabel(result.confidence) : null;
  const credibilityConfidence = credibility?.status === 'completed' ? confidenceLabel(credibility.confidence) : null;
  const rows = [
    ['01 · Вывод модели', aiLabel, aiUnavailable ? 'text-mv-text-secondary' : status[result.verdict]],
    ['02 · Метод анализа', modelName, 'text-mv-text'],
    ...(aiConfidence ? [['03 · Уверенность модели', aiConfidence.replace('Уверенность модели: ', ''), 'text-mv-text']] : []),
    ...(validProcessingMs(result.processing_ms) ? [['04 · Время обработки', `${result.processing_ms} мс`, 'text-mv-text']] : []),
  ];
  const credibilityRows = credibility?.status === 'completed'
    ? [
        ['01 · Вывод модели', credibilityLabels[credibility.verdict || 'MIXED_CREDIBILITY'], 'text-mv-text'],
        ['02 · Метод анализа', displayModelName(credibility.model || '—'), 'text-mv-text'],
        ...(credibilityConfidence ? [['03 · Уверенность модели', credibilityConfidence.replace('Уверенность модели: ', ''), 'text-mv-text']] : []),
        ...(validProcessingMs(credibility.processing_ms) ? [['04 · Время обработки', `${credibility.processing_ms} мс`, 'text-mv-text']] : []),
      ]
    : credibility
      ? [
          ['01 · Статус', 'Временно недоступна', 'text-mv-text-secondary'],
          ['02 · Метод анализа', displayModelName(credibility.model || '—'), 'text-mv-text'],
          ...(validProcessingMs(credibility.processing_ms) ? [['03 · Время обработки', `${credibility.processing_ms} мс`, 'text-mv-text']] : []),
        ]
      : [];

  return <article className={`${isComplex ? 'complex-check-report' : ''} bg-white border border-black/[.09] rounded-[20px] shadow-[0_1px_2px_rgba(0,0,0,.04),0_24px_60px_rgba(0,0,0,.07)] overflow-hidden`}>
    <header className="p-6 sm:p-9 flex flex-col sm:flex-row sm:items-start justify-between gap-6"><div className="min-w-0"><p className="eyebrow mb-3">Отчёт о проверке · {new Date().toLocaleDateString('ru-RU')}</p><h2 className="text-2xl font-semibold tracking-[-.035em]">Результат анализа</h2><p className="text-xs text-mv-text-muted mt-2 break-words">Метод · {modelName}</p></div><div className="sm:text-right min-w-0"><p className={`text-sm break-words ${aiUnavailable ? 'text-mv-text-secondary' : status[result.verdict]}`}>{aiLabel}</p>{authenticityIndex !== null && <><p className="text-5xl font-semibold tracking-[-.06em] mt-2">{authenticityIndex}<span className="text-xl text-mv-text-muted">%</span></p><p className="text-xs text-mv-text-muted mt-1">индекс подлинности</p>{aiConfidence && <p className="text-xs text-mv-text-muted mt-2">{aiConfidence}</p>}</>}</div></header>
    <div className="border-t border-black/[.07] p-6 sm:p-9 grid lg:grid-cols-[1fr_300px] gap-10"><div className="min-w-0"><p className="eyebrow mb-5">Общий вывод</p><h3 className="text-2xl sm:text-3xl font-semibold tracking-[-.04em] leading-tight">{aiLabel}</h3><p className="text-mv-text-secondary leading-7 mt-4 max-w-xl break-words">{result.explanation}</p></div><aside className="bg-[#f4f4f2] border border-black/[.06] rounded-[14px] p-5"><p className="text-xs font-semibold mb-3">Рекомендация</p><p className="text-sm text-mv-text-secondary leading-6">Используйте результат вместе с контекстом материала. Для значимых решений проведите ручную проверку источника.</p></aside></div>
    {result.short_report && <section className="complex-print-section px-6 sm:px-9 pb-8"><p className="eyebrow mb-3">{isComplex ? 'Итог' : 'Краткий отчёт'}</p><p className="max-w-3xl text-sm sm:text-base text-mv-text-secondary leading-7 break-words">{result.short_report}</p></section>}
    {isComplex && <section className="complex-print-section px-6 sm:px-9 pb-8"><div className="border-t border-black/[.07] pt-7"><p className="eyebrow mb-3">Анализ происхождения текста</p>{result.ai_details?.signals.length ? <div className="mt-6"><p className="eyebrow mb-3">Ключевые признаки</p><ol className="space-y-4 text-sm text-mv-text-secondary leading-6">{result.ai_details.signals.map((signal, index) => <li className="complex-print-keep break-words" key={`${signal.type}-${index}`}><span className="font-semibold text-mv-text">{String(index + 1).padStart(2, '0')} · {severityLabels[signal.severity]} · {signal.title}</span><br />{signal.explanation}</li>)}</ol></div> : null}{result.ai_details?.human_signals.length ? <div className="mt-6"><p className="eyebrow mb-3">Что говорит в пользу человеческого авторства</p><ul className="space-y-2 text-sm text-mv-text-secondary leading-6">{result.ai_details.human_signals.map((signal, index) => <li className="complex-print-keep break-words" key={index}>• {signal}</li>)}</ul></div> : null}</div></section>}
    {credibility && <section className="complex-print-section px-6 sm:px-9 pb-8"><div className="border-t border-black/[.07] pt-7"><div className="flex items-end justify-between gap-4"><div className="min-w-0"><p className="eyebrow mb-2">Достоверность</p><h3 className="text-xl font-semibold tracking-[-.03em] break-words">{credibilityIndex === null ? 'Проверка временно недоступна' : credibilityLabels[credibility.verdict || 'MIXED_CREDIBILITY']}</h3>{credibilityConfidence && <p className="text-xs text-mv-text-muted mt-2">{credibilityConfidence}</p>}</div>{credibilityIndex !== null && <p className="text-3xl font-semibold tracking-[-.05em] shrink-0">{credibilityIndex}<span className="text-base text-mv-text-muted">%</span></p>}</div>{credibilityIndex !== null && <div className="h-2 rounded-full bg-[#ecece9] mt-5 overflow-hidden"><div className="h-full bg-mv-text rounded-full" style={{ width: `${credibilityIndex}%` }} /></div>}<p className="text-mv-text-secondary leading-7 mt-4 max-w-3xl break-words">{credibility.summary}</p>{credibility.issues.length > 0 && <div className="mt-6"><p className="eyebrow mb-3">{isComplex ? 'Ключевые проблемы' : 'Ключевые несоответствия'}</p><ol className="space-y-3 text-sm text-mv-text-secondary leading-6">{credibility.issues.map((issue, index) => <li className="complex-print-keep break-words" key={`${issue.type}-${index}`}><span className="font-semibold text-mv-text">{isComplex ? `${String(index + 1).padStart(2, '0')} · ${severityLabels[issue.severity]} · ` : `${index + 1}. `}{issue.claim}</span><br />{issue.explanation}</li>)}</ol></div>}{isComplex && credibility.credible_points?.length ? <div className="mt-6"><p className="eyebrow mb-3">Что выглядит правдоподобно</p><ul className="space-y-2 text-sm text-mv-text-secondary leading-6">{credibility.credible_points.map((point, index) => <li className="complex-print-keep break-words" key={index}>• {point}</li>)}</ul></div> : null}{!isComplex && credibility.sources.length > 0 && <div className="mt-6"><p className="eyebrow mb-3">Источники</p><ol className="space-y-2 text-sm">{credibility.sources.map((source, index) => { const url = safeUrl(source.url); return <li key={`${source.url}-${index}`}>{url ? <a className="text-mv-text underline underline-offset-4" href={url} target="_blank" rel="noreferrer">[{index + 1}] {source.title}</a> : <span className="text-mv-text-secondary">[{index + 1}] {source.title}</span>}</li>; })}</ol></div>}</div></section>}
    <section className="complex-print-section px-6 sm:px-9 pb-8"><p className="eyebrow mb-3">Основные признаки</p>{rows.map(row => <div key={row[0]} className="complex-print-keep grid sm:grid-cols-2 gap-2 py-4 border-t border-black/[.07] text-sm"><span className="text-mv-text-secondary">{row[0]}</span><span className={`${row[2]} sm:text-right min-w-0 break-words`}>{row[1]}</span></div>)}</section>
    {credibilityRows.length > 0 && <section className="complex-print-section px-6 sm:px-9 pb-8"><p className="eyebrow mb-3">Проверка достоверности</p>{credibilityRows.map(row => <div key={row[0]} className="complex-print-keep grid sm:grid-cols-2 gap-2 py-4 border-t border-black/[.07] text-sm"><span className="text-mv-text-secondary">{row[0]}</span><span className={`${row[2]} sm:text-right min-w-0 break-words`}>{row[1]}</span></div>)}</section>}
    <footer className={`${isComplex ? 'complex-export-control' : ''} px-6 sm:px-9 py-5 bg-[#fafaf9] border-t border-black/[.07] flex justify-end`}><button className="btn-black !min-h-[40px]" onClick={printResult}><Download size={15} />Экспортировать PDF</button></footer>
  </article>;
}
