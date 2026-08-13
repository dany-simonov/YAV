import { useEffect, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';

import { CheckResultCard } from '../../components/CheckResultCard';
import { Card } from '../../components/ui';
import { loadCheckFromHistory } from '../../lib/checkHistory';
import { useAuthStore } from '../../store';
import type { Check } from '../../types';

export function HistoryDetailPage() {
  const { checkId = '' } = useParams();
  const { user } = useAuthStore();
  const [check, setCheck] = useState<Check | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!user?.$id || !checkId) {
      setError('Проверка не найдена');
      setLoading(false);
      return undefined;
    }

    setLoading(true);
    setError(null);
    loadCheckFromHistory(user.$id, checkId)
      .then((item) => {
        if (!cancelled) setCheck(item);
      })
      .catch((loadError: unknown) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'Не удалось открыть проверку');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [checkId, user?.$id]);

  return (
    <div className="max-w-6xl mx-auto space-y-6 pb-20">
      <Link
        to="/dashboard/history"
        className="inline-flex items-center gap-2 text-sm text-mv-text-secondary hover:text-mv-text transition-colors"
      >
        <ArrowLeft className="w-4 h-4" />
        Вернуться к истории
      </Link>

      {loading && <Card className="text-center py-16">Загрузка результата...</Card>}

      {!loading && error && (
        <Card className="text-center py-16">
          <h1 className="text-xl font-semibold text-mv-text">Не удалось открыть проверку</h1>
          <p className="mt-3 text-mv-text-secondary">{error}</p>
        </Card>
      )}

      {!loading && check && (
        <>
          <div>
            <p className="eyebrow mb-3">История проверок</p>
            <h1 className="text-2xl font-bold text-mv-text">Сохранённый результат</h1>
            <p className="mt-2 text-sm text-mv-text-secondary">
              {new Date(check.created_at).toLocaleString('ru-RU')}
            </p>
          </div>
          <CheckResultCard result={check} />
        </>
      )}
    </div>
  );
}
