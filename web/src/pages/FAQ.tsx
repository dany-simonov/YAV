import { useState } from 'react';
import { ChevronDown } from 'lucide-react';

const CONTACT_EMAIL = 'yav.app@yandex.ru';

interface FAQItem {
  question: string;
  answer: string;
}

interface FAQCategory {
  title: string;
  items: FAQItem[];
}

const faqData: FAQCategory[] = [
  {
    title: 'Общие вопросы',
    items: [
      {
        question: 'Что такое ЯВЬ?',
        answer: 'ЯВЬ — это платформа верификации медиаконтента, которая использует модели машинного обучения для определения, был ли контент (фото, аудио, текст) создан человеком или сгенерирован искусственным интеллектом.',
      },
      {
        question: 'Как работает проверка?',
        answer: 'Вы загружаете файл или вставляете текст — система анализирует его с помощью специализированных ML-моделей и возвращает вердикт (REAL/FAKE/UNCERTAIN), Индекс подлинности (0-100%) и текстовое объяснение результата.',
      },
      {
        question: 'Какие форматы поддерживаются?',
        answer: 'Фотографии (JPEG, PNG, WebP), аудио (MP3, OGG, WAV) и текст. Видеоанализ временно недоступен и будет добавлен в ближайших версиях.',
      },
    ],
  },
  {
    title: 'Точность и модели',
    items: [
      {
        question: 'Насколько точна система?',
        answer: 'Точность зависит от типа контента: фото — 94.4% (Sightengine), аудио — 99.5% (Resemble Detect). Для текстового Gemini-анализа система показывает вероятностную оценку и может вернуть UNCERTAIN при недостатке контекста.',
      },
      {
        question: 'Какие модели используются?',
        answer: 'Мы используем комбинацию коммерческих API (Sightengine, Resemble, Gemini, AIOrNot) и open-source моделей с HuggingFace как fallback. Это обеспечивает высокую надёжность и отказоустойчивость.',
      },
      {
        question: 'Что такое Индекс подлинности?',
        answer: 'Индекс подлинности (0-100%) показывает, насколько вероятно, что контент создан человеком. 100% — точно подлинный, 0% — точно сгенерирован ИИ. Для AI-контента индекс автоматически инвертируется для интуитивного понимания.',
      },
    ],
  },
  {
    title: 'Безопасность и приватность',
    items: [
      {
        question: 'Сохраняются ли мои файлы?',
        answer: 'Нет. Файлы обрабатываются в момент анализа и сразу удаляются. Мы не храним ваши медиафайлы и не передаём их третьим лицам.',
      },
      {
        question: 'Кто видит результаты проверок?',
        answer: 'Только вы. История проверок привязана к вашему аккаунту и недоступна другим пользователям. В Free-тарифе история хранится только в текущей сессии.',
      },
    ],
  },
  {
    title: 'Тарифы и лимиты',
    items: [
      {
        question: 'Сколько проверок можно сделать бесплатно?',
        answer: '3 проверки в день в Free-тарифе. Лимит обновляется каждые 24 часа.',
      },
      {
        question: 'Когда будет доступен Premium?',
        answer: 'Premium-тариф находится в разработке. Вы можете встать в лист ожидания, написав нам на почту.',
      },
      {
        question: 'Есть ли API для интеграции?',
        answer: 'REST API для B2B-клиентов будет доступен в версии v0.7.0. Свяжитесь с нами для получения раннего доступа.',
      },
    ],
  },
];

export function FAQ() {
  const [openItems, setOpenItems] = useState<Set<string>>(new Set());

  const toggleItem = (id: string) => {
    setOpenItems(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  return (
    <div className="pt-24 pb-16">
      <div className="container">
        {/* Hero */}
        <div className="text-center mb-16">
          <h1 className="text-4xl font-bold text-mv-text mb-4">Часто задаваемые вопросы</h1>
          <p className="text-lg text-mv-text-secondary">
            Ответы на популярные вопросы о платформе ЯВЬ
          </p>
        </div>

        {/* FAQ Accordion */}
        <div className="max-w-3xl mx-auto space-y-8">
          {faqData.map((category, catIndex) => (
            <div key={catIndex}>
              <h2 className="text-xl font-semibold text-mv-accent mb-4">{category.title}</h2>
              <div className="space-y-3">
                {category.items.map((item, itemIndex) => {
                  const id = `${catIndex}-${itemIndex}`;
                  const isOpen = openItems.has(id);

                  return (
                    <div
                      key={id}
                      className="bg-mv-surface border border-mv-border rounded-lg overflow-hidden"
                    >
                      <button
                        onClick={() => toggleItem(id)}
                        className="w-full flex items-center justify-between p-4 text-left hover:bg-mv-surface-2 transition-colors"
                      >
                        <span className="font-medium text-mv-text pr-4">{item.question}</span>
                        <ChevronDown className={`w-5 h-5 text-mv-accent transition-transform ${isOpen ? 'rotate-180' : ''}`} />
                      </button>
                      {isOpen && (
                        <div className="px-4 pb-4">
                          <p className="text-mv-text-secondary leading-relaxed">{item.answer}</p>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        {/* Contact */}
        <div className="max-w-3xl mx-auto mt-16">
          <div className="bg-mv-surface border border-mv-border rounded-xl p-8 text-center">
            <h2 className="text-xl font-semibold text-mv-text mb-3">Не нашли ответ?</h2>
            <p className="text-mv-text-secondary mb-6">
              Напишите нам — мы ответим в течение 24 часов
            </p>
            <a
              href={`mailto:${CONTACT_EMAIL}`}
              className="inline-block px-6 py-3 bg-mv-accent text-white rounded-lg font-medium hover:bg-mv-accent-hover transition-colors"
            >
              Написать на {CONTACT_EMAIL}
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
