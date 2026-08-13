import { Card, CardHeader, Button } from '../../components/ui';

const CONTACT_EMAIL = 'yav.app@yandex.ru';
const ENTERPRISE_EMAIL_SUBJECT = 'Заявка на корпоративный тариф ЯВЬ';
const ENTERPRISE_EMAIL_BODY = `Здравствуйте, команда ЯВЬ!

Хочу обсудить подключение корпоративного тарифа ЯВЬ.

Компания:
Имя и должность:
Количество пользователей:
Примерное количество проверок в месяц:
Какие возможности необходимы:

Контактный телефон:

Буду ждать вашего ответа.`;

const ENTERPRISE_MAILTO_LINK = `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(ENTERPRISE_EMAIL_SUBJECT)}&body=${encodeURIComponent(ENTERPRISE_EMAIL_BODY)}`;

export function ApiSettingsPage() {
  const freeFeatures = [
    'Доступен всем пользователям',
    'Базовые проверки контента',
    'Понятный результат анализа',
    'История последних проверок',
  ];

  const enterpriseFeatures = [
    'Индивидуальные лимиты проверок',
    'Расширенный доступ к API',
    'Командная работа и управление доступом',
    'Приоритетная техническая поддержка',
    'Договор и индивидуальные условия SLA',
  ];

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <Card>
        <CardHeader
          title="Тарифные планы"
          description="Бесплатная проверка для всех и корпоративное решение для команд"
        />

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="p-6 md:p-8 rounded-xl border border-mv-accent bg-mv-accent/5">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-xl font-semibold text-mv-text">Бесплатный</h3>
              <span className="px-2 py-1 rounded-full border border-mv-border text-[11px] font-semibold tracking-wide text-mv-text-muted">
                ДЛЯ ВСЕХ
              </span>
            </div>

            <div className="mt-4 text-3xl font-bold text-mv-text">0 ₽</div>

            <ul className="mt-6 space-y-3">
              {freeFeatures.map((feature) => (
                <li key={feature} className="flex items-center gap-2 text-sm">
                  <div className="w-4 h-4 rounded-full bg-mv-accent/10 flex items-center justify-center">
                    <div className="w-1.5 h-1.5 rounded-full bg-mv-accent" />
                  </div>
                  <span className="text-mv-text-secondary">{feature}</span>
                </li>
              ))}
            </ul>

          </div>

          <div className="p-6 md:p-8 rounded-xl border border-mv-border hover:border-mv-text-muted transition-colors">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-xl font-semibold text-mv-text">Enterprise</h3>
              <span className="px-2 py-1 rounded-full border border-mv-border text-[11px] font-semibold tracking-wide text-mv-text-muted">
                B2B
              </span>
            </div>

            <div className="mt-4 text-3xl font-bold text-mv-text">По запросу</div>
            <p className="mt-2 text-sm text-mv-text-muted">
              Стоимость зависит от задач и объёма проверок
            </p>

            <ul className="mt-6 space-y-3">
              {enterpriseFeatures.map((feature) => (
                <li key={feature} className="flex items-center gap-2 text-sm">
                  <div className="w-4 h-4 rounded-full bg-mv-accent/10 flex items-center justify-center">
                    <div className="w-1.5 h-1.5 rounded-full bg-mv-accent" />
                  </div>
                  <span className="text-mv-text-secondary">{feature}</span>
                </li>
              ))}
            </ul>

            <Button
              fullWidth
              className="mt-6"
              onClick={() => { window.location.href = ENTERPRISE_MAILTO_LINK; }}
            >
              Обсудить подключение
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
