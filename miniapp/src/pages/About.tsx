import { StatBar } from '../components/StatBar'

export function About() {
  return (
    <div className="min-h-screen bg-mv-bg p-4 pb-24">
      {/* Logo & Title */}
      <div className="flex flex-col items-center text-center mb-8">
        <img src="/yav-logo.png" alt="ЯВЬ" className="w-24 h-24 p-1 rounded-2xl bg-white border border-black/15 object-contain shadow-md" />
        <h1 className="text-2xl font-bold text-mv-text">ЯВЬ</h1>
        <p className="text-mv-text-secondary">Верификация медиаконтента</p>
      </div>

      {/* Model Accuracy */}
      <div className="bg-mv-card rounded-2xl p-5 mb-6">
        <h3 className="text-mv-text font-medium mb-4">Точность моделей</h3>
        <StatBar label="Фото (Sightengine)" value={94.4} color="#6C63FF" />
        <StatBar label="Аудио (Resemble)" value={99.5} color="#00C48C" />
        <StatBar label="Видео (CLIP pipeline)" value={81} color="#FFB800" />
      </div>

      {/* How it works */}
      <div className="bg-mv-card rounded-2xl p-5 mb-6">
        <h3 className="text-mv-text font-medium mb-4">Как это работает</h3>
        <div className="space-y-4">
          {[
            'Вы отправляете файл боту',
            'Файл анализируется специализированной моделью в памяти сервера',
            'Вы получаете вердикт с уровнем уверенности',
          ].map((step, i) => (
            <div key={i} className="flex items-start gap-3">
              <div className="w-6 h-6 rounded-full bg-mv-accent flex items-center justify-center text-white text-sm font-medium flex-shrink-0">
                {i + 1}
              </div>
              <p className="text-mv-text-secondary text-sm pt-0.5">{step}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Privacy */}
      <div className="bg-mv-card rounded-2xl p-5 mb-6">
        <h3 className="text-mv-text font-medium mb-3">Приватность</h3>
        <p className="text-mv-text-secondary text-sm leading-relaxed">
          Файлы обрабатываются исключительно в оперативной памяти сервера и не сохраняются на диске. 
          Персональные данные не передаются третьим лицам.
        </p>
      </div>

      {/* Version */}
      <p className="text-center text-mv-text-secondary text-xs">
        v0.5.0 · ЯВЬ
      </p>
    </div>
  )
}
