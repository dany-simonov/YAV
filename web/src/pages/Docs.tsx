import { AlertTriangle, ArrowRight, Check, Code2, Server } from 'lucide-react';

const CONTACT_EMAIL = 'yav.app@yandex.ru';

const endpoints = [
  {
    method: 'POST', path: '/analyze', title: 'Одиночная проверка файла',
    description: 'Принимает один файл, определяет тип медиаконтента и возвращает вероятностный результат анализа.',
    params: [['file','File','обязательный','Изображение, аудио, видео или поддерживаемый файл'],['user_id','integer','обязательный','Идентификатор пользователя'],['username','string','необязательный','Имя пользователя'],['first_name','string','необязательный','Отображаемое имя'],['text_content','string','необязательный','Текстовое содержимое для маршрутизации']],
    response: `{
  "verdict": "UNCERTAIN",
  "confidence": 0.76,
  "model_used": "sightengine",
  "explanation": "Обнаружены неоднозначные признаки",
  "media_type": "image",
  "processing_ms": 1240
}`,
  },
  {
    method: 'POST', path: '/analyze/text/hybrid', title: 'Глубокая проверка текста',
    description: 'Выполняет детекцию AI-признаков и фактчекинг текста. Минимальная длина — 50 символов.',
    params: [['text','string (JSON)','обязательный','Текст для гибридного анализа']],
    response: `{
  "verdict": "UNCERTAIN",
  "ai_verdict": "HUMAN",
  "ai_confidence": 0.31,
  "model_used": "hybrid",
  "processing_ms": 2180,
  "fact_checks": [],
  "tokens": []
}`,
  },
  {
    method: 'POST', path: '/bigcheck', title: 'Пакетная проверка',
    description: 'Проверяет до 10 элементов и формирует общий результат кросс-анализа.',
    params: [['files','File[]','обязательный','Один или несколько файлов'],['user_id','integer','обязательный','Идентификатор пользователя'],['text_content','string','необязательный','Дополнительный текст для анализа']],
    response: `{
  "overall_verdict": "UNCERTAIN",
  "overall_confidence": 0.67,
  "authenticity_index": 67,
  "summary": "Однозначный вердикт вынести невозможно",
  "results": [],
  "total_files": 2,
  "total_processing_ms": 3460
}`,
  },
  {
    method: 'GET', path: '/health', title: 'Состояние API',
    description: 'Liveness-проверка сервиса. Не запускает ML-анализ.', params: [],
    response: `{
  "status": "ok",
  "version": "0.5.0"
}`,
  },
];

export function Docs() {
  return <div className="pt-32 pb-24">
    <div className="container">
      <div className="grid lg:grid-cols-[.9fr_1.1fr] gap-10 items-end pb-16 border-b border-black/[.08]">
        <div><p className="eyebrow mb-6">ЯВЬ / API</p><h1 className="section-title">Документация<br/>платформы</h1></div>
        <div className="lg:ml-auto max-w-xl"><p className="text-lg leading-8 text-mv-text-secondary">Актуальные REST-эндпоинты для проверки медиа и текста. Внешний production-доступ предоставляется после согласования.</p><div className="mt-6 flex items-start gap-3 text-sm text-mv-uncertain"><AlertTriangle size={17} className="mt-0.5 shrink-0"/><span>API развивается. Формат отдельных полей может измениться до публикации стабильной версии.</span></div></div>
      </div>

      <section className="py-14 grid md:grid-cols-2 gap-5">
        <article className="bg-white border border-black/[.08] rounded-2xl p-7"><Server size={20}/><p className="eyebrow mt-8 mb-3">Локальная разработка</p><code className="text-sm">http://localhost:8000</code><p className="mt-4 text-sm leading-6 text-mv-text-secondary">Интерактивная OpenAPI-схема FastAPI доступна по адресу <code>/docs</code>.</p></article>
        <article className="bg-white border border-black/[.08] rounded-2xl p-7"><Code2 size={20}/><p className="eyebrow mt-8 mb-3">Авторизация</p><code className="text-sm">x-api-secret: YOUR_API_SECRET</code><p className="mt-4 text-sm leading-6 text-mv-text-secondary">Секрет передаётся в заголовке каждого аналитического запроса. `/health` доступен без него.</p></article>
      </section>

      <section className="pb-14"><p className="eyebrow mb-6">Быстрый старт</p><div className="bg-[#0b0b0b] text-white rounded-2xl p-6 overflow-x-auto"><pre className="text-sm leading-7 font-mono text-white/75">{`curl -X POST http://localhost:8000/analyze \\
  -H "x-api-secret: YOUR_API_SECRET" \\
  -F "file=@photo.jpg" \\
  -F "user_id=123" \\
  -F "username=example"`}</pre></div></section>

      <section><div className="flex items-end justify-between gap-6 mb-7"><div><p className="eyebrow mb-4">Reference</p><h2 className="text-3xl font-semibold tracking-[-.04em]">Эндпоинты</h2></div><span className="text-sm text-mv-text-muted">Версия 0.5.0</span></div>
        <div className="space-y-5">{endpoints.map(endpoint=><article key={endpoint.path} className="bg-white border border-black/[.08] rounded-[18px] overflow-hidden">
          <header className="p-6 sm:p-7 flex flex-col sm:flex-row sm:items-start gap-4 border-b border-black/[.07]"><span className={`w-fit px-2.5 py-1 rounded-md text-[11px] font-bold ${endpoint.method==='GET'?'bg-mv-real/10 text-mv-real':'bg-black text-white'}`}>{endpoint.method}</span><div><code className="font-semibold">{endpoint.path}</code><h3 className="text-xl font-semibold tracking-[-.025em] mt-4">{endpoint.title}</h3><p className="text-sm text-mv-text-secondary leading-6 mt-2">{endpoint.description}</p></div></header>
          {endpoint.params.length>0&&<div className="p-6 sm:p-7 border-b border-black/[.07] overflow-x-auto"><h4 className="text-sm font-semibold mb-4">Параметры</h4><table className="w-full min-w-[650px] text-sm"><thead><tr className="text-left text-mv-text-muted"><th className="pb-3 font-medium">Имя</th><th className="pb-3 font-medium">Тип</th><th className="pb-3 font-medium">Статус</th><th className="pb-3 font-medium">Описание</th></tr></thead><tbody>{endpoint.params.map(param=><tr key={param[0]} className="border-t border-black/[.07]"><td className="py-3 font-mono">{param[0]}</td><td className="py-3 text-mv-text-secondary">{param[1]}</td><td className="py-3"><span className="flex items-center gap-1.5"><Check size={13}/>{param[2]}</span></td><td className="py-3 text-mv-text-secondary">{param[3]}</td></tr>)}</tbody></table></div>}
          <div className="p-6 sm:p-7"><h4 className="text-sm font-semibold mb-4">Пример ответа</h4><pre className="bg-[#f4f4f2] rounded-xl p-5 overflow-x-auto text-sm leading-6 font-mono text-mv-text-secondary">{endpoint.response}</pre></div>
        </article>)}</div>
      </section>

      <section className="mt-16 bg-white border border-black/[.08] rounded-[20px] p-8 sm:p-10 flex flex-col sm:flex-row sm:items-center justify-between gap-7 shadow-[0_2px_3px_rgba(0,0,0,.04),0_18px_44px_rgba(0,0,0,.06)]"><div><h2 className="text-2xl font-semibold tracking-[-.035em]">Нужен production-доступ?</h2><p className="mt-3 text-mv-text-secondary">Напишите команде ЯВЬ для согласования интеграции.</p></div><a href={`mailto:${CONTACT_EMAIL}?subject=YAV%20API%20Access`} className="btn-black shrink-0">{CONTACT_EMAIL}<ArrowRight size={16}/></a></section>
    </div>
  </div>;
}
