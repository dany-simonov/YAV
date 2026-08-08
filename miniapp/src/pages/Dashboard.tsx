import { useEffect, useState } from 'react'
import WebApp from '@twa-dev/sdk'
import { useTelegramUser } from '../hooks/useTelegramUser'
import { getUserStats } from '../api/client'
import type { UserStats } from '../types'
import { Image, AudioWaveform, Video, FileText } from 'lucide-react'

const howToUse = [
  { icon: Image, text: 'Фото — детекция AI-генерации (94.4%)' },
  { icon: AudioWaveform, text: 'Аудио — синтетическая речь (99.5%)' },
  { icon: Video, text: 'Видео — покадровый анализ (81%)' },
  { icon: FileText, text: 'Текст — написан ИИ (98%)' },
]

export function Dashboard() {
  const { id, firstName, photoUrl } = useTelegramUser()
  const [stats, setStats] = useState<UserStats | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (id) {
      getUserStats(id)
        .then(setStats)
        .catch(console.error)
        .finally(() => setLoading(false))
    } else {
      setLoading(false)
    }
  }, [id])

  const handleOpenBot = () => {
    WebApp.close()
  }

  const checksProgress = stats
    ? Math.min((stats.checks_today / stats.daily_limit) * 100, 100)
    : 0
  const isLimitReached = stats ? stats.checks_today >= stats.daily_limit : false

  return (
    <div className="min-h-screen bg-mv-bg p-4 pb-24">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-2">
          <img src="/yav-logo.png" alt="ЯВЬ" className="w-12 h-12 p-0.5 rounded-xl bg-white border border-black/15 object-contain shadow-sm" />
          <span className="text-lg font-semibold text-mv-text">ЯВЬ</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-mv-text-secondary text-sm">{firstName}</span>
          {photoUrl ? (
            <img src={photoUrl} alt="" className="w-8 h-8 rounded-full" />
          ) : (
            <div className="w-8 h-8 rounded-full bg-mv-accent flex items-center justify-center text-white text-sm font-medium">
              {firstName[0]}
            </div>
          )}
        </div>
      </div>

      {/* User Card */}
      <div className="bg-mv-card rounded-2xl p-5 mb-6">
        {loading ? (
          <div className="space-y-3 animate-pulse">
            <div className="h-6 bg-white/10 rounded w-1/3" />
            <div className="h-4 bg-white/10 rounded w-2/3" />
            <div className="h-2 bg-white/10 rounded" />
          </div>
        ) : stats ? (
          <>
            <div className="flex items-center justify-between mb-3">
              <span className="text-mv-text font-medium">
                {stats.is_premium ? 'Premium' : 'Free'}
              </span>
              <span className="text-mv-text-secondary text-sm">
                {stats.total_checks} проверок всего
              </span>
            </div>
            <div className="mb-2">
              <div className="flex justify-between text-sm mb-1">
                <span className="text-mv-text-secondary">Сегодня</span>
                <span className={isLimitReached ? 'text-mv-fake' : 'text-mv-text'}>
                  {stats.checks_today} / {stats.daily_limit}
                </span>
              </div>
              <div className="h-2 bg-white/10 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${
                    isLimitReached ? 'bg-mv-fake' : 'bg-mv-accent'
                  }`}
                  style={{ width: `${checksProgress}%` }}
                />
              </div>
            </div>
            {isLimitReached && (
              <p className="text-mv-fake text-xs mt-2">Лимит исчерпан</p>
            )}
          </>
        ) : (
          <p className="text-mv-text-secondary">Отправьте файл боту для начала</p>
        )}
      </div>

      {/* How to use */}
      <div className="bg-mv-card rounded-2xl p-5 mb-6">
        <h3 className="text-mv-text font-medium mb-4">Как использовать</h3>
        <div className="space-y-3">
          {howToUse.map((item, i) => (
            <div key={i} className="flex items-center gap-3">
              <item.icon className="w-6 h-6 text-mv-text-secondary" />
              <span className="text-mv-text-secondary text-sm">{item.text}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Open Bot Button */}
      <button
        onClick={handleOpenBot}
        className="w-full bg-mv-accent text-white font-medium py-4 rounded-2xl hover:bg-mv-accent/90 transition-colors"
      >
        Открыть бот
      </button>
    </div>
  )
}
