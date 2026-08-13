import { useAuth } from '../hooks';
import { Link } from 'react-router-dom';

export function Dashboard() {
  const { user } = useAuth();

  if (!user) {
    return (
      <div className="pt-24 pb-16">
        <div className="container text-center">
          <h1 className="text-3xl font-bold text-mv-text mb-4">Личный кабинет</h1>
          <p className="text-mv-text-secondary mb-8">
            Войдите, чтобы получить доступ к истории проверок и статистике
          </p>
          <Link
            to="/"
            className="inline-block px-6 py-3 bg-mv-accent text-white rounded-lg font-medium hover:bg-mv-accent-hover transition-colors"
          >
            Вернуться на главную
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="pt-24 pb-16">
      <div className="container">
        <div className="max-w-4xl mx-auto">
          {/* Header */}
          <div className="flex items-center gap-4 mb-8">
            <div className="w-16 h-16 rounded-full bg-gradient-to-br from-mv-accent to-teal-400 flex items-center justify-center text-white text-2xl font-bold">
              {user.name.charAt(0).toUpperCase()}
            </div>
            <div>
              <h1 className="text-2xl font-bold text-mv-text">{user.name}</h1>
              <p className="text-mv-text-secondary">{user.email}</p>
            </div>
            <span className="ml-auto px-3 py-1 bg-mv-surface border border-mv-border rounded-full text-sm text-mv-text-secondary">
              Free план
            </span>
          </div>

          {/* Stats Grid */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
            <div className="bg-mv-surface border border-mv-border rounded-xl p-6">
              <div className="text-3xl font-bold text-mv-accent mb-1">0 / 3</div>
              <div className="text-sm text-mv-text-secondary">Проверок сегодня</div>
              <div className="mt-3 h-2 bg-mv-surface-2 rounded-full overflow-hidden">
                <div className="h-full bg-mv-accent rounded-full" style={{ width: '0%' }} />
              </div>
            </div>
            <div className="bg-mv-surface border border-mv-border rounded-xl p-6">
              <div className="text-3xl font-bold text-mv-text mb-1">0</div>
              <div className="text-sm text-mv-text-secondary">Всего проверок</div>
            </div>
            <div className="bg-mv-surface border border-mv-border rounded-xl p-6">
              <div className="text-3xl font-bold text-mv-real mb-1">—</div>
              <div className="text-sm text-mv-text-secondary">Средний индекс</div>
            </div>
          </div>

          {/* History */}
          <div className="bg-mv-surface border border-mv-border rounded-xl p-6">
            <h2 className="text-xl font-semibold text-mv-text mb-4">История проверок</h2>
            <div className="text-center py-12">
              <div className="text-4xl mb-4">📋</div>
              <p className="text-mv-text-secondary mb-4">
                Здесь появится история ваших проверок
              </p>
              <Link
                to="/#bigcheck"
                className="inline-block px-6 py-3 bg-mv-accent text-white rounded-lg font-medium hover:bg-mv-accent-hover transition-colors"
              >
                Сделать первую проверку
              </Link>
            </div>
          </div>

          {/* Accuracy Table */}
          <div className="mt-8 bg-mv-surface border border-mv-border rounded-xl p-6">
            <h2 className="text-xl font-semibold text-mv-text mb-4">Точность моделей</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-mv-border">
                    <th className="text-left py-3 text-mv-text-secondary font-medium">Тип контента</th>
                    <th className="text-left py-3 text-mv-text-secondary font-medium">Модель</th>
                    <th className="text-right py-3 text-mv-text-secondary font-medium">Точность</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b border-mv-border">
                    <td className="py-3 text-mv-text">🖼️ Фото</td>
                    <td className="py-3 text-mv-text-secondary">Sightengine</td>
                    <td className="py-3 text-right text-mv-real font-medium">94.4%</td>
                  </tr>
                  <tr className="border-b border-mv-border">
                    <td className="py-3 text-mv-text">🎵 Аудио</td>
                    <td className="py-3 text-mv-text-secondary">Resemble Detect</td>
                    <td className="py-3 text-right text-mv-real font-medium">99.5%</td>
                  </tr>
                  <tr className="border-b border-mv-border">
                    <td className="py-3 text-mv-text">🎬 Видео</td>
                    <td className="py-3 text-mv-text-secondary">FFmpeg + CLIP</td>
                    <td className="py-3 text-right text-mv-uncertain font-medium">81%</td>
                  </tr>
                  <tr>
                    <td className="py-3 text-mv-text">📄 Текст</td>
                    <td className="py-3 text-mv-text-secondary">Gemini Text Verification</td>
                    <td className="py-3 text-right text-mv-text-secondary font-medium">—</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
