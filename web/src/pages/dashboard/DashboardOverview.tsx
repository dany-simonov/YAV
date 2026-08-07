/**
 * Dashboard Overview Page
 * =======================
 * Главная страница личного кабинета с обзором статистики.
 */

import { Link } from 'react-router-dom';
import { useMemo } from 'react';
import { Plus, ArrowRight, ArrowUp, Shield, Clock, CheckCircle, FileText, Image, AudioWaveform, Video, Hand } from 'lucide-react';
import { Card, CardHeader, Button } from '../../components/ui';
import { getHistoryStats } from '../../lib/checkHistory';
import { useAuthStore } from '../../store';

export function DashboardOverview() {
  const { user } = useAuthStore();
  const dailyLimit = 3;

  const stats = useMemo(() => {
    if (!user?.$id) {
      return {
        checksToday: 0,
        dailyLimit,
        totalChecks: 0,
        averageIndex: null as number | null,
        checksThisWeek: 0,
      };
    }

    const historyStats = getHistoryStats(user.$id);
    return {
      checksToday: historyStats.checksToday,
      dailyLimit,
      totalChecks: historyStats.totalChecks,
      averageIndex: historyStats.averageIndex,
      checksThisWeek: historyStats.checksThisWeek,
    };
  }, [user?.$id]);

  return (
    <div className="max-w-6xl mx-auto space-y-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-mv-text flex items-center gap-2">
            Добро пожаловать, {user?.name?.split(' ')[0] || 'Пользователь'}! <Hand className="w-6 h-6 text-yellow-400" />
          </h1>
          <p className="mt-1 text-mv-text-secondary">
            Вот обзор вашей активности за сегодня
          </p>
        </div>
        
        <Link to="/dashboard/check">
          <Button className="text-white" leftIcon={<Plus className="w-4 h-4" />}>
            Новая проверка
          </Button>
        </Link>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Checks Today */}
        <Card className="relative overflow-hidden">
          <div className="flex items-start justify-between">
            <div>
              <p className="text-sm text-mv-text-secondary">Проверок сегодня</p>
              <p className="text-3xl font-bold text-mv-text mt-1">
                {stats.checksToday} / {stats.dailyLimit}
              </p>
            </div>
            <div className="w-10 h-10 rounded-lg bg-mv-accent/10 flex items-center justify-center">
              <Shield className="w-5 h-5 text-mv-accent" />
            </div>
          </div>
          <div className="mt-4 h-2 bg-mv-surface-2 rounded-full overflow-hidden">
            <div
              className="h-full bg-mv-accent rounded-full transition-all duration-500"
              style={{ width: `${Math.min((stats.checksToday / stats.dailyLimit) * 100, 100)}%` }}
            />
          </div>
        </Card>

        {/* Total Checks */}
        <Card>
          <div className="flex items-start justify-between">
            <div>
              <p className="text-sm text-mv-text-secondary">Всего проверок</p>
              <p className="text-3xl font-bold text-mv-text mt-1">{stats.totalChecks}</p>
            </div>
            <div className="w-10 h-10 rounded-lg bg-mv-accent/10 flex items-center justify-center">
              <CheckCircle className="w-5 h-5 text-mv-accent" />
            </div>
          </div>
          {stats.totalChecks > 0 && (
            <p className="mt-4 text-sm text-mv-real flex items-center gap-1">
              <ArrowUp className="w-4 h-4" />
              {stats.checksThisWeek} за эту неделю
            </p>
          )}
        </Card>

        {/* Average Index */}
        <Card>
          <div className="flex items-start justify-between">
            <div>
              <p className="text-sm text-mv-text-secondary">Средний индекс</p>
              <p className="text-3xl font-bold text-mv-real mt-1">
                {stats.averageIndex !== null ? `${stats.averageIndex}%` : '—'}
              </p>
            </div>
            <div className="w-10 h-10 rounded-lg bg-mv-real/10 flex items-center justify-center">
              <ArrowUp className="w-5 h-5 text-mv-real" />
            </div>
          </div>
          <p className="mt-4 text-sm text-mv-text-muted">
            Индекс подлинности
          </p>
        </Card>

        {/* Plan */}
        <Card className="bg-[#111] border-black text-white">
          <div className="flex items-start justify-between">
            <div>
              <p className="text-sm text-white/55">Ваш план</p>
              <p className="text-xl font-bold text-white mt-1">Free</p>
            </div>
            <div className="w-10 h-10 rounded-lg bg-white/10 flex items-center justify-center">
              <Clock className="w-5 h-5 text-white" />
            </div>
          </div>
          <Link
            to="/dashboard/api"
            className="mt-4 text-sm text-white/70 font-medium flex items-center gap-1 hover:text-white"
          >
            Увеличить лимит
            <ArrowRight className="w-4 h-4" />
          </Link>
        </Card>
      </div>

      {/* Quick Actions */}
      <Card>
        <CardHeader
          title="Быстрые действия"
          description="Начните работу с проверки медиаконтента"
        />
        
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <Link
            to="/dashboard/check"
            className="p-4 rounded-lg bg-mv-surface-2 border border-mv-border hover:border-mv-accent hover:bg-mv-accent/5 transition-all group"
          >
            <div className="w-10 h-10 rounded-lg bg-mv-accent/10 flex items-center justify-center mb-3 group-hover:bg-mv-accent/20 transition-colors">
              <Shield className="w-5 h-5 text-mv-accent" />
            </div>
            <h3 className="font-medium text-mv-text">Проверить медиа</h3>
            <p className="text-sm text-mv-text-muted mt-1">
              Загрузите фото, аудио или видео
            </p>
          </Link>

          <Link
            to="/dashboard/check?tab=text"
            className="p-4 rounded-lg bg-mv-surface-2 border border-mv-border hover:border-mv-accent hover:bg-mv-accent/5 transition-all group"
          >
            <div className="w-10 h-10 rounded-lg bg-mv-accent/10 flex items-center justify-center mb-3 group-hover:bg-mv-accent/20 transition-colors">
              <FileText className="w-5 h-5 text-mv-accent" />
            </div>
            <h3 className="font-medium text-mv-text">Проверить текст</h3>
            <p className="text-sm text-mv-text-muted mt-1">
              Определить ChatGPT и другие LLM
            </p>
          </Link>

          <Link
            to="/dashboard/history"
            className="p-4 rounded-lg bg-mv-surface-2 border border-mv-border hover:border-mv-accent hover:bg-mv-accent/5 transition-all group"
          >
            <div className="w-10 h-10 rounded-lg bg-mv-accent/10 flex items-center justify-center mb-3 group-hover:bg-mv-accent/20 transition-colors">
              <Clock className="w-5 h-5 text-mv-accent" />
            </div>
            <h3 className="font-medium text-mv-text">История</h3>
            <p className="text-sm text-mv-text-muted mt-1">
              Посмотреть прошлые проверки
            </p>
          </Link>
        </div>
      </Card>

      {/* Big Text Check Callout */}
      <Card className="border-mv-border bg-white">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div className="flex items-start gap-4">
            <div className="w-12 h-12 rounded-xl bg-mv-accent/20 flex items-center justify-center">
              <img src="/assets/img/logo.png" alt="" className="w-7 h-7" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-mv-text">Большая проверка текста</h3>
              <p className="text-sm text-mv-text-secondary mt-1">
                Двойная проверка: детектор ИИ + фактчек/заимствования с пословной подсветкой.
              </p>
              <p className="text-xs text-mv-text-muted mt-2">
                Рекомендуем 200-2000 символов. Чем больше текст, тем точнее проверка.
              </p>
            </div>
          </div>
          <Link to="/dashboard/big-text">
            <Button className="text-white" leftIcon={<FileText className="w-4 h-4" />}>
              Открыть проверку
            </Button>
          </Link>
        </div>
      </Card>

      {/* Model Accuracy Table */}
      <Card>
        <CardHeader
          title="Точность моделей"
          description="Актуальные показатели наших ИИ-детекторов"
        />
        
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-mv-border">
                <th className="text-left py-3 px-4 text-sm font-medium text-mv-text-secondary">Тип контента</th>
                <th className="text-left py-3 px-4 text-sm font-medium text-mv-text-secondary">Модель</th>
                <th className="text-right py-3 px-4 text-sm font-medium text-mv-text-secondary">Точность</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b border-mv-border">
                <td className="py-3 px-4">
                  <span className="flex items-center gap-2">
                    <Image className="w-5 h-5 text-mv-text-secondary" />
                    <span className="text-mv-text">Фото</span>
                  </span>
                </td>
                <td className="py-3 px-4 text-mv-text-secondary">Sightengine</td>
                <td className="py-3 px-4 text-right">
                  <span className="text-mv-real font-semibold">94.4%</span>
                </td>
              </tr>
              <tr className="border-b border-mv-border">
                <td className="py-3 px-4">
                  <span className="flex items-center gap-2">
                    <AudioWaveform className="w-5 h-5 text-mv-text-secondary" />
                    <span className="text-mv-text">Аудио</span>
                  </span>
                </td>
                <td className="py-3 px-4 text-mv-text-secondary">Resemble Detect</td>
                <td className="py-3 px-4 text-right">
                  <span className="text-mv-real font-semibold">99.5%</span>
                </td>
              </tr>
              <tr className="border-b border-mv-border">
                <td className="py-3 px-4">
                  <span className="flex items-center gap-2">
                    <Video className="w-5 h-5 text-mv-text-secondary" />
                    <span className="text-mv-text">Видео</span>
                  </span>
                </td>
                <td className="py-3 px-4 text-mv-text-secondary">FFmpeg + CLIP</td>
                <td className="py-3 px-4 text-right">
                  <span className="text-mv-uncertain font-semibold">81%</span>
                </td>
              </tr>
              <tr>
                <td className="py-3 px-4">
                  <span className="flex items-center gap-2">
                    <FileText className="w-5 h-5 text-mv-text-secondary" />
                    <span className="text-mv-text">Текст</span>
                  </span>
                </td>
                <td className="py-3 px-4 text-mv-text-secondary">Sapling AI</td>
                <td className="py-3 px-4 text-right">
                  <span className="text-mv-real font-semibold">98%</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
