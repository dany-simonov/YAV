import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertCircle, CheckCircle2, Clock, FileText, ShieldCheck } from 'lucide-react';

import { Card, CardHeader, Button, Alert } from '../../components/ui';
import { TextInput } from '../../components/upload';
import { functions, APPWRITE_CONFIG } from '../../lib/appwrite';
import {
  AnalysisExecutionError,
  analysisErrorMessageFromUnknown,
  parseAnalysisBackendError,
} from '../../lib/analysisError';
import { cn } from '../../lib/utils';
import { safeExternalUrl } from '../../lib/safeExternalUrl';
import { useAuthStore } from '../../store';
import type { HybridTextResult, HybridToken } from '../../types';

const MIN_LENGTH = 200;
const MAX_LENGTH = 10000;
const RECOMMENDED_RANGE = { min: 200, max: 2000 };

const tokenColors: Record<HybridToken['type'], string> = {
  normal: 'bg-mv-real/10 text-mv-real',
  manipulation: 'bg-mv-uncertain/10 text-mv-uncertain',
  fake: 'bg-mv-fake/15 text-mv-fake',
  plagiarism: 'bg-mv-fake/10 text-mv-fake',
};

const formatDuration = (seconds: number) => {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
};

const normalizeAiConfidence = (value: number) => {
  if (!Number.isFinite(value)) return 0;
  if (value <= 1) return Math.round(value * 100);
  return Math.round(value);
};

const splitIntoWordSpans = (text: string, className: string, keyPrefix: string) => {
  return text.split(/(\s+)/).map((chunk, index) => {
    if (chunk.trim().length === 0) {
      return (
        <span key={`${keyPrefix}-space-${index}`}>{chunk}</span>
      );
    }

    return (
      <span
        key={`${keyPrefix}-word-${index}`}
        className={cn('rounded px-0.5', className)}
      >
        {chunk}
      </span>
    );
  });
};

/** Complex text mode rendered inside the "New check" workspace. */
export function ComplexTextCheck() {
  const navigate = useNavigate();
  const { user } = useAuthStore();

  const [text, setText] = useState('');
  const [result, setResult] = useState<HybridTextResult | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  useEffect(() => {
    if (!isAnalyzing) {
      setElapsedSeconds(0);
      return undefined;
    }

    const timer = setInterval(() => {
      setElapsedSeconds((prev) => prev + 1);
    }, 1000);

    return () => clearInterval(timer);
  }, [isAnalyzing]);

  const canSubmit = text.length >= MIN_LENGTH && text.length <= MAX_LENGTH && !isAnalyzing;

  const highlightedTokens = useMemo(() => {
    if (!result?.tokens?.length) return [] as HybridToken[];
    return result.tokens;
  }, [result]);

  const handleSubmit = async () => {
    if (!canSubmit) return;
    if (!user?.$id) {
      setError('Для запуска проверки требуется авторизация.');
      return;
    }

    setError(null);
    setResult(null);
    setIsAnalyzing(true);

    try {
      const payload = {
        text,
        userId: user.$id,
        username: user.name,
        firstName: user.name.split(' ')[0] || '',
        mediaType: 'text',
        mode: 'hybrid_text',
        sourceLabel: text.slice(0, 120).replace(/\s+/g, ' ').trim(),
      };

      const execution = await functions.createExecution(
        APPWRITE_CONFIG.functions.analyze,
        JSON.stringify(payload),
        false
      );

      let responseBody = execution.responseBody || '';
      let responseStatusCode = execution.responseStatusCode;

      if (!responseBody && execution.$id) {
        const maxAttempts = 20;
        for (let i = 0; i < maxAttempts; i += 1) {
          await new Promise((resolve) => setTimeout(resolve, 1500));
          const refreshed = await functions.getExecution(
            APPWRITE_CONFIG.functions.analyze,
            execution.$id
          );
          responseStatusCode = refreshed.responseStatusCode;
          if (refreshed.responseBody) {
            responseBody = refreshed.responseBody;
            break;
          }
          if (refreshed.status && refreshed.status !== 'processing') {
            break;
          }
        }
      }

      if (!responseBody) {
        throw new AnalysisExecutionError(null);
      }

      const data = JSON.parse(responseBody);
      const backendError = parseAnalysisBackendError(data);
      if (backendError?.code === 'email_not_verified') {
        navigate('/verify-email', {
          replace: true,
          state: { notice: 'Подтвердите email перед запуском анализа.' },
        });
        return;
      }
      if (backendError || (responseStatusCode && responseStatusCode >= 400)) {
        throw new AnalysisExecutionError(backendError);
      }

      setResult(data as HybridTextResult);
    } catch (err) {
      setError(analysisErrorMessageFromUnknown(err));
    } finally {
      setIsAnalyzing(false);
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Комплексная проверка текста"
          description="Чем больше текста, тем точнее результат. Модель может ошибаться — используйте результат как ориентир."
        />

        <div className="space-y-5">
          <TextInput
            value={text}
            onChange={setText}
            minLength={MIN_LENGTH}
            maxLength={MAX_LENGTH}
            recommendedRange={RECOMMENDED_RANGE}
            disabled={isAnalyzing}
          />

          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div className="flex items-center gap-3 text-sm text-mv-text-secondary">
              <ShieldCheck className="w-4 h-4 text-mv-accent" />
              <span>Минимум {MIN_LENGTH} символов, максимум {MAX_LENGTH.toLocaleString()}.</span>
            </div>
            <Button
              onClick={handleSubmit}
              disabled={!canSubmit}
              className="text-white"
            >
              {isAnalyzing ? 'Идет проверка...' : 'Запустить проверку'}
            </Button>
          </div>

          {isAnalyzing && (
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 p-4 rounded-lg bg-mv-surface-2 border border-mv-border">
              <div className="flex items-center gap-2 text-mv-text-secondary">
                <Clock className="w-4 h-4" />
                <span>Глубокая проверка может занять больше времени. Прошло: {formatDuration(elapsedSeconds)}</span>
              </div>
              <div className="text-xs text-mv-text-muted">
                Чем длиннее текст, тем дольше анализ.
              </div>
            </div>
          )}

          {error && (
            <Alert variant="error" className="mt-2">
              {error}
            </Alert>
          )}
        </div>
      </Card>

      {result && (
        <div className="space-y-6">
          <Card>
            <CardHeader
              title="Результат глубокой проверки"
              description="Подсветка слов отражает итог: зеленое — безопасно, желтое — сомнительно, красное — ошибка или заимствование."
            />

            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-4 text-sm">
                <div className="flex items-center gap-2 text-mv-text-secondary">
                  <CheckCircle2 className="w-4 h-4 text-mv-real" />
                  <span>Индекс ИИ: {normalizeAiConfidence(result.ai_confidence)}%</span>
                </div>
                <div className="flex items-center gap-2 text-mv-text-secondary">
                  <AlertCircle className="w-4 h-4 text-mv-uncertain" />
                  <span>Вердикт ИИ: {result.ai_verdict}</span>
                </div>
                <div className="flex items-center gap-2 text-mv-text-secondary">
                  <FileText className="w-4 h-4 text-mv-accent" />
                  <span>Модель: {result.model_used}</span>
                </div>
                <div className="flex items-center gap-2 text-mv-text-secondary">
                  <Clock className="w-4 h-4" />
                  <span>{result.processing_ms} мс</span>
                </div>
              </div>

              {result.truncated && (
                <div className="flex items-center gap-2 text-xs text-mv-uncertain">
                  <AlertCircle className="w-4 h-4" />
                  Текст был сокращен до {MAX_LENGTH.toLocaleString()} символов для анализа.
                </div>
              )}

              <div className="p-4 rounded-lg bg-mv-surface-2 border border-mv-border">
                <div className="flex flex-wrap gap-3 text-xs text-mv-text-muted mb-3">
                  <span className="inline-flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full bg-mv-real/60" />
                    Норма
                  </span>
                  <span className="inline-flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full bg-mv-uncertain/70" />
                    Сомнительно
                  </span>
                  <span className="inline-flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full bg-mv-fake/70" />
                    Ошибка / заимствование
                  </span>
                </div>

                <div className="leading-relaxed text-sm text-mv-text">
                  {highlightedTokens.length > 0
                    ? highlightedTokens.map((token, index) => (
                        <span key={`token-${index}`}>
                          {splitIntoWordSpans(token.text, tokenColors[token.type], `token-${index}`)}
                        </span>
                      ))
                    : splitIntoWordSpans(text, tokenColors.normal, 'fallback')}
                </div>
              </div>
            </div>
          </Card>

          {result.fact_checks?.length > 0 && (
            <Card>
              <CardHeader
                title="Фактчекинг и заимствования"
                description="Ключевые фрагменты с пояснениями и источниками."
              />

              <div className="space-y-4">
                {result.fact_checks.map((item, index) => (
                  <div key={`${item.exact_quote}-${index}`} className="p-4 rounded-lg bg-mv-surface-2 border border-mv-border">
                    <p className="text-sm font-medium text-mv-text mb-2">{item.exact_quote}</p>
                    <p className="text-sm text-mv-text-secondary mb-2">{item.truth}</p>
                    {safeExternalUrl(item.source_url) && (
                      <a
                        href={safeExternalUrl(item.source_url) || undefined}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-mv-accent hover:underline"
                      >
                        {safeExternalUrl(item.source_url)}
                      </a>
                    )}
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
