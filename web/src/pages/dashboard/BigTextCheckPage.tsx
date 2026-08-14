import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Clock, ShieldCheck, Sparkles } from 'lucide-react';

import { Card, CardHeader, Button, Alert } from '../../components/ui';
import { CheckResultCard } from '../../components/CheckResultCard';
import { TextInput } from '../../components/upload';
import { functions, APPWRITE_CONFIG } from '../../lib/appwrite';
import { AnalysisExecutionError, analysisErrorMessageFromUnknown, parseAnalysisBackendError } from '../../lib/analysisError';
import { useAuthStore } from '../../store';
import type { CheckResult } from '../../types';

const MIN_LENGTH = 200;
const MAX_LENGTH = 10000;
const RECOMMENDED_RANGE = { min: 200, max: 2000 };

const formatDuration = (seconds: number) => `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;

export function BigTextCheckPage() {
  const navigate = useNavigate();
  const { user } = useAuthStore();
  const [text, setText] = useState('');
  const [result, setResult] = useState<CheckResult | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  useEffect(() => {
    if (!isAnalyzing) { setElapsedSeconds(0); return undefined; }
    const timer = setInterval(() => setElapsedSeconds((previous) => previous + 1), 1000);
    return () => clearInterval(timer);
  }, [isAnalyzing]);

  const canSubmit = text.length >= MIN_LENGTH && text.length <= MAX_LENGTH && !isAnalyzing;
  const handleSubmit = async () => {
    if (!canSubmit) return;
    if (!user?.$id) { setError('Для запуска проверки требуется авторизация.'); return; }
    setError(null); setResult(null); setIsAnalyzing(true);
    try {
      const execution = await functions.createExecution(APPWRITE_CONFIG.functions.analyze, JSON.stringify({
        text, userId: user.$id, username: user.name, firstName: user.name.split(' ')[0] || '',
        mediaType: 'text', mode: 'hybrid_text', sourceLabel: text.slice(0, 120).replace(/\s+/g, ' ').trim(),
      }), false);
      let responseBody = execution.responseBody || '';
      let responseStatusCode = execution.responseStatusCode;
      if (!responseBody && execution.$id) {
        for (let attempt = 0; attempt < 20; attempt += 1) {
          await new Promise((resolve) => setTimeout(resolve, 1500));
          const refreshed = await functions.getExecution(APPWRITE_CONFIG.functions.analyze, execution.$id);
          responseStatusCode = refreshed.responseStatusCode;
          if (refreshed.responseBody) { responseBody = refreshed.responseBody; break; }
          if (refreshed.status && refreshed.status !== 'processing') break;
        }
      }
      if (!responseBody) throw new AnalysisExecutionError(null);
      const data = JSON.parse(responseBody);
      const backendError = parseAnalysisBackendError(data);
      if (backendError?.code === 'email_not_verified') {
        navigate('/verify-email', { replace: true, state: { notice: 'Подтвердите email перед запуском анализа.' } });
        return;
      }
      if (backendError || (responseStatusCode && responseStatusCode >= 400)) throw new AnalysisExecutionError(backendError);
      setResult(data as CheckResult);
    } catch (err) { setError(analysisErrorMessageFromUnknown(err)); }
    finally { setIsAnalyzing(false); }
  };

  return <div className="max-w-6xl mx-auto space-y-6">
    <div className="flex flex-col gap-2"><h1 className="text-2xl font-bold text-mv-text flex items-center gap-2">Комплексный анализ <Sparkles className="w-5 h-5 text-mv-accent" /></h1><p className="text-mv-text-secondary">Две независимые проверки: происхождение текста и его достоверность. Рекомендуемый объём: {RECOMMENDED_RANGE.min}–{RECOMMENDED_RANGE.max} символов, максимум {MAX_LENGTH.toLocaleString()}.</p></div>
    <Card><CardHeader title="Текст для комплексного анализа" description="Результат — ориентир: учитывайте контекст и источник материала." /><div className="space-y-5"><TextInput value={text} onChange={setText} minLength={MIN_LENGTH} maxLength={MAX_LENGTH} recommendedRange={RECOMMENDED_RANGE} disabled={isAnalyzing} /><div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3"><div className="flex items-center gap-3 text-sm text-mv-text-secondary"><ShieldCheck className="w-4 h-4 text-mv-accent" /><span>Минимум {MIN_LENGTH} символов, максимум {MAX_LENGTH.toLocaleString()}.</span></div><Button onClick={handleSubmit} disabled={!canSubmit} className="text-white">{isAnalyzing ? 'Идёт анализ...' : 'Запустить анализ'}</Button></div>{isAnalyzing && <div className="flex items-center gap-2 p-4 rounded-lg bg-mv-surface-2 border border-mv-border text-mv-text-secondary"><Clock className="w-4 h-4" /><span>Комплексный анализ выполняется параллельно. Прошло: {formatDuration(elapsedSeconds)}</span></div>}{error && <Alert variant="error">{error}</Alert>}</div></Card>
    {result && <CheckResultCard result={result} />}
  </div>;
}
