/**
 * History Page
 * ============
 * Страница истории проверок пользователя.
 */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { 
  Search, 
  Filter, 
  FileImage, 
  FileAudio, 
  FileVideo, 
  FileText,
  ChevronRight,
  Clock,
  Plus,
  Trash2
} from 'lucide-react';
import { Card, CardHeader, Button, Input } from '../../components/ui';
import { cn } from '../../lib/utils';
import {
  clearChecksHistory,
  deleteCheckFromHistory,
  loadChecksHistory,
} from '../../lib/checkHistory';
import { useAuthStore } from '../../store';
import type { Check, Verdict, MediaType } from '../../types';

const verdictConfig: Record<Verdict, { label: string; color: string; bgColor: string }> = {
  REAL: { label: 'Реальный', color: 'text-mv-real', bgColor: 'bg-mv-real/10' },
  FAKE: { label: 'ИИ', color: 'text-mv-fake', bgColor: 'bg-mv-fake/10' },
  UNCERTAIN: { label: 'Неопределено', color: 'text-mv-uncertain', bgColor: 'bg-mv-uncertain/10' },
};

const mediaTypeConfig: Record<MediaType, { icon: React.ReactNode; label: string }> = {
  image: { icon: <FileImage className="w-4 h-4" />, label: 'Фото' },
  audio: { icon: <FileAudio className="w-4 h-4" />, label: 'Аудио' },
  video: { icon: <FileVideo className="w-4 h-4" />, label: 'Видео' },
  text: { icon: <FileText className="w-4 h-4" />, label: 'Текст' },
};

export function HistoryPage() {
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<MediaType | 'all'>('all');
  const [checks, setChecks] = useState<Check[]>([]);
  const [loading, setLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const { user } = useAuthStore();

  useEffect(() => {
    let cancelled = false;
    if (!user?.$id) {
      setChecks([]);
      setLoading(false);
      return undefined;
    }
    setLoading(true);
    setHistoryError(null);
    loadChecksHistory(user.$id)
      .then((items) => {
        if (!cancelled) setChecks(items);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setHistoryError(error instanceof Error ? error.message : 'Не удалось загрузить историю');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [user?.$id]);

  const handleDelete = async (checkId: string) => {
    if (!user?.$id) return;
    setHistoryError(null);
    try {
      await deleteCheckFromHistory(user.$id, checkId);
      setChecks((current) => current.filter((check) => check.id !== checkId));
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : 'Не удалось удалить проверку');
    }
  };

  const handleClear = async () => {
    if (!user?.$id) return;
    setHistoryError(null);
    try {
      await clearChecksHistory(user.$id);
      setChecks([]);
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : 'Не удалось очистить историю');
    }
  };

  // Filter checks
  const filteredChecks = checks.filter((check) => {
    if (filter !== 'all' && check.media_type !== filter) return false;
    if (search && !check.explanation.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-mv-text">История проверок</h1>
          <p className="mt-1 text-mv-text-secondary">
            Все ваши проверки в одном месте
          </p>
        </div>
        
        <div className="flex gap-2">
          {checks.length > 0 && (
            <Button variant="secondary" onClick={handleClear} leftIcon={<Trash2 className="w-4 h-4" />}>
              Очистить
            </Button>
          )}
          <Link to="/dashboard/check">
            <Button className="text-white" leftIcon={<Plus className="w-4 h-4" />}>
              Новая проверка
            </Button>
          </Link>
        </div>
      </div>

      {historyError && <p className="text-sm text-mv-fake">{historyError}</p>}

      {/* Filters */}
      <Card padding="sm" className="flex flex-col sm:flex-row gap-4">
        <div className="flex-1">
          <Input
            placeholder="Поиск по истории..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            leftIcon={<Search className="w-4 h-4" />}
            size="sm"
          />
        </div>
        
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-mv-text-muted" />
          {(['all', 'image', 'audio', 'video', 'text'] as const).map((type) => (
            <button
              key={type}
              onClick={() => setFilter(type)}
              className={cn(
                'px-3 py-1.5 text-sm rounded-lg transition-colors',
                filter === type
                  ? 'bg-mv-accent text-white'
                  : 'bg-mv-surface-2 text-mv-text-secondary hover:text-mv-text'
              )}
            >
              {type === 'all' ? 'Все' : mediaTypeConfig[type].label}
            </button>
          ))}
        </div>
      </Card>

      {/* Results */}
      {loading ? (
        <Card className="text-center py-16">Загрузка истории...</Card>
      ) : filteredChecks.length > 0 ? (
        <Card padding="none">
          <div className="divide-y divide-mv-border">
            {filteredChecks.map((check) => {
              const verdict = verdictConfig[check.verdict];
              const mediaType = mediaTypeConfig[check.media_type];
              
              return (
                <div
                  key={check.id}
                  className="flex items-center gap-4 p-4 hover:bg-mv-surface-2/50 transition-colors cursor-pointer"
                >
                  {/* Media Type Icon */}
                  <div className="w-10 h-10 rounded-lg bg-mv-surface-2 flex items-center justify-center text-mv-text-muted">
                    {mediaType.icon}
                  </div>
                  
                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-mv-text truncate">
                        {check.explanation}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-mv-text-muted">
                      <span className="flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {new Date(check.created_at).toLocaleDateString('ru-RU')}
                      </span>
                      <span>{mediaType.label}</span>
                      <span>{check.model_used}</span>
                    </div>
                  </div>
                  
                  {/* Verdict Badge */}
                  <div className={cn('px-3 py-1 rounded-full text-xs font-medium', verdict.bgColor, verdict.color)}>
                    {verdict.label}
                  </div>
                  
                  {/* Confidence */}
                  <div className="text-right">
                    <div className="text-lg font-semibold text-mv-text">
                      {check.confidence}%
                    </div>
                    <div className="text-xs text-mv-text-muted">уверенность</div>
                  </div>

                  <button
                    type="button"
                    onClick={() => handleDelete(check.id)}
                    className="p-2 text-mv-text-muted hover:text-mv-fake"
                    aria-label="Удалить проверку"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                  
                  <ChevronRight className="w-5 h-5 text-mv-text-muted" />
                </div>
              );
            })}
          </div>
        </Card>
      ) : (
        <Card className="text-center py-16">
          <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-mv-surface-2 flex items-center justify-center">
            <Clock className="w-8 h-8 text-mv-text-muted" />
          </div>
          <h3 className="text-lg font-semibold text-mv-text mb-2">
            История пуста
          </h3>
          <p className="text-mv-text-secondary mb-6 max-w-md mx-auto">
            {search || filter !== 'all'
              ? 'По вашему запросу ничего не найдено'
              : 'Здесь появятся ваши проверки. Начните с анализа медиафайла или текста.'
            }
          </p>
          {!search && filter === 'all' && (
            <Link to="/dashboard/check">
              <Button leftIcon={<Plus className="w-4 h-4" />}>
                Создать первую проверку
              </Button>
            </Link>
          )}
        </Card>
      )}

      {/* Stats Summary */}
      {checks.length > 0 && (
        <Card>
          <CardHeader
            title="Сводка"
            description="Статистика по всем проверкам"
          />
          
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div className="p-4 rounded-lg bg-mv-surface-2 text-center">
              <div className="text-2xl font-bold text-mv-text">{checks.length}</div>
              <div className="text-sm text-mv-text-muted">Всего</div>
            </div>
            <div className="p-4 rounded-lg bg-mv-real/10 text-center">
              <div className="text-2xl font-bold text-mv-real">
                {checks.filter((c) => c.verdict === 'REAL').length}
              </div>
              <div className="text-sm text-mv-text-muted">Реальных</div>
            </div>
            <div className="p-4 rounded-lg bg-mv-fake/10 text-center">
              <div className="text-2xl font-bold text-mv-fake">
                {checks.filter((c) => c.verdict === 'FAKE').length}
              </div>
              <div className="text-sm text-mv-text-muted">ИИ</div>
            </div>
            <div className="p-4 rounded-lg bg-mv-uncertain/10 text-center">
              <div className="text-2xl font-bold text-mv-uncertain">
                {checks.filter((c) => c.verdict === 'UNCERTAIN').length}
              </div>
              <div className="text-sm text-mv-text-muted">Неопределено</div>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
