import { useEffect, useMemo, useState } from 'react';
import { BarChart3 } from 'lucide-react';
import { adminApi, type ProviderKey, type ProviderUsageHistory } from '../../lib/admin';

const providers: Array<{ value: ProviderKey; label: string }> = [
  { value: 'gemini', label: 'Gemini' }, { value: 'sightengine', label: 'Sightengine' }, { value: 'aiornot', label: 'AI or Not' }, { value: 'sapling', label: 'Sapling' }, { value: 'resemble', label: 'Resemble' },
];
const periods: Array<7 | 30 | 90> = [7, 30, 90];
const number = new Intl.NumberFormat('ru-RU');

export function ProviderUsageChart() {
  const [provider, setProvider] = useState<ProviderKey>('gemini');
  const [days, setDays] = useState<7 | 30 | 90>(30);
  const [history, setHistory] = useState<ProviderUsageHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { let active = true; setHistory(null); setError(null); void adminApi.getProviderUsageHistory(provider, days).then((result) => { if (active) setHistory(result); }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : 'Не удалось загрузить статистику'); }); return () => { active = false; }; }, [provider, days]);
  return <section className="mt-6 rounded-2xl border border-black/10 bg-white p-5"><div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"><div><div className="flex items-center gap-2"><BarChart3 className="h-4 w-4" /><h2 className="font-semibold">Расход API по времени</h2></div><p className="mt-1 text-sm text-mv-text-secondary">Один столбик — один день. Данные берутся только из серверных счётчиков Appwrite.</p></div><div className="flex flex-wrap gap-2"><label className="sr-only" htmlFor="provider-history">Нейросеть</label><select id="provider-history" value={provider} onChange={(event) => setProvider(event.target.value as ProviderKey)} className="h-10 rounded-[10px] border border-black/[.09] bg-white px-3 text-sm">{providers.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>{periods.map((item) => <button key={item} type="button" aria-pressed={days === item} onClick={() => setDays(item)} className={`h-10 rounded-[10px] px-3 text-sm font-medium ${days === item ? 'bg-black text-white' : 'border border-black/[.09] bg-white'}`}>{item} дн.</button>)}</div></div>{error ? <p role="alert" className="mt-5 rounded-xl border border-black/[.09] bg-[#f7f7f6] p-4 text-sm text-mv-text-secondary">{error}</p> : history ? <UsageGraph history={history} /> : <div className="mt-5 h-64 animate-pulse rounded-xl bg-black/[.05]" />}</section>;
}

function UsageGraph({ history }: { history: ProviderUsageHistory }) {
  const chart = useMemo(() => {
    const width = 760; const height = 260; const left = 54; const right = 18; const top = 18; const bottom = 40;
    const max = Math.max(history.daily_limit, ...history.points.map((point) => point.used), 1);
    const step = (width - left - right) / Math.max(1, history.points.length);
    const y = (value: number) => top + (height - top - bottom) * (1 - value / max);
    return { width, height, left, right, top, bottom, max, step, y };
  }, [history]);
  const ticks = [0, .5, 1].map((ratio) => Math.round(chart.max * ratio));
  const labels = history.points.filter((_, index) => index === 0 || index === history.points.length - 1 || (history.points.length > 12 && index === Math.floor(history.points.length / 2))).map((point) => point.date.slice(5).split('-').reverse().join('.'));
  return <div className="mt-5"><div className="flex flex-wrap items-baseline justify-between gap-3"><p className="text-sm text-mv-text-secondary">Единица: {history.unit}</p><p className="text-lg font-semibold tabular-nums">Всего за период: {number.format(history.points.reduce((sum, point) => sum + point.used, 0))}</p></div><svg viewBox={`0 0 ${chart.width} ${chart.height}`} className="mt-3 h-auto w-full" role="img" aria-label={`Столбчатый график расхода ${history.provider} за период`}><title>Расход {history.provider}: один столбик — один день</title>{ticks.map((tick) => <g key={tick}><line x1={chart.left} x2={chart.width - chart.right} y1={chart.y(tick)} y2={chart.y(tick)} stroke="rgba(0,0,0,.09)" /><text x={chart.left - 8} y={chart.y(tick) + 4} textAnchor="end" fontSize="11" fill="#737373">{number.format(tick)}</text></g>)}<line x1={chart.left} x2={chart.width - chart.right} y1={chart.y(history.daily_limit)} y2={chart.y(history.daily_limit)} stroke="#C58A17" strokeDasharray="4 4" />{history.points.map((point, index) => { const width = Math.max(2, chart.step * .62); const x = chart.left + chart.step * index + (chart.step - width) / 2; const top = chart.y(point.used); const base = chart.height - chart.bottom; return <rect key={point.date} x={x} y={top} width={width} height={Math.max(0, base - top)} rx="2" fill={point.used > history.daily_limit ? '#C83E56' : '#0A0A0A'}><title>{`${point.date}: ${number.format(point.used)} ${history.unit}`}</title></rect>; })}<text x={chart.width - chart.right} y={chart.y(history.daily_limit) - 6} textAnchor="end" fontSize="11" fill="#C58A17">дневной лимит</text>{labels.map((label, index) => <text key={`${label}-${index}`} x={index === 0 ? chart.left : index === labels.length - 1 ? chart.width - chart.right : chart.left + (chart.width - chart.left - chart.right) / 2} y={chart.height - 12} textAnchor={index === 0 ? 'start' : index === labels.length - 1 ? 'end' : 'middle'} fontSize="11" fill="#737373">{label}</text>)}</svg></div>;
}
