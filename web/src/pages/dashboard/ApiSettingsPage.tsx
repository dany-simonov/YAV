/**
 * API Settings Page
 * =================
 * Страница настроек API для B2B клиентов.
 */

import { useState } from 'react';
import { 
  Key, 
  Copy, 
  Eye, 
  EyeOff, 
  RefreshCw, 
  ExternalLink,
  Code,
  Zap,
  Shield
} from 'lucide-react';
import { Card, CardHeader, Button, Alert } from '../../components/ui';
import { cn } from '../../lib/utils';

export function ApiSettingsPage() {
  const [showKey, setShowKey] = useState(false);
  const [copied, setCopied] = useState(false);
  
  // Mock API key placeholder (will be replaced with real key from backend)
  const apiKey = 'ist_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx';
  const maskedKey = 'ist_xxxx...xxxx';

  const handleCopy = async () => {
    await navigator.clipboard.writeText(apiKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const plans = [
    {
      name: 'Free',
      price: '0 ₽',
      period: '',
      features: ['3 проверки в день', 'API недоступно', 'Базовый вердикт'],
      current: true,
      cta: 'Текущий план',
    },
    {
      name: 'Pro',
      price: '199 ₽',
      period: '/мес',
      features: ['100 проверок в месяц', 'API доступ', 'Приоритетная поддержка', 'PDF-отчеты'],
      current: false,
      cta: 'Оформить подписку',
      popular: true,
    },
    {
      name: 'Business',
      price: '999 ₽',
      period: '/мес',
      features: ['1000 проверок в месяц', 'API с высоким лимитом', 'Webhooks', 'Dedicated support'],
      current: false,
      cta: 'Связаться с нами',
    },
  ];

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-mv-text">Настройки API</h1>
        <p className="mt-1 text-mv-text-secondary">
          Управление API-ключами и интеграция
        </p>
      </div>

      {/* Current Plan Banner */}
      <Alert variant="info" title="Ваш текущий план: Free">
        Для доступа к API необходимо перейти на план Pro или выше
      </Alert>

      {/* API Key Section */}
      <Card>
        <CardHeader
          title="API ключ"
          description="Используйте этот ключ для аутентификации запросов к API"
          action={
            <Button
              variant="ghost"
              size="sm"
              leftIcon={<RefreshCw className="w-4 h-4" />}
              disabled
            >
              Обновить
            </Button>
          }
        />

        <div className="flex items-center gap-4 p-4 bg-mv-surface-2 rounded-lg border border-mv-border">
          <Key className="w-5 h-5 text-mv-text-muted flex-shrink-0" />
          
          <code className="flex-1 font-mono text-sm text-mv-text-muted">
            {showKey ? apiKey : maskedKey}
          </code>
          
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowKey(!showKey)}
              className="p-2 rounded-lg text-mv-text-muted hover:text-mv-text hover:bg-mv-surface transition-colors"
              title={showKey ? 'Скрыть' : 'Показать'}
            >
              {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
            
            <button
              onClick={handleCopy}
              className={cn(
                'p-2 rounded-lg transition-colors',
                copied 
                  ? 'text-mv-real bg-mv-real/10' 
                  : 'text-mv-text-muted hover:text-mv-text hover:bg-mv-surface'
              )}
              title="Копировать"
            >
              <Copy className="w-4 h-4" />
            </button>
          </div>
        </div>

        <p className="mt-4 text-sm text-mv-text-muted">
          ⚠️ Никогда не передавайте API-ключ третьим лицам и не публикуйте его в коде
        </p>
      </Card>

      {/* Pricing Plans */}
      <Card>
        <CardHeader
          title="Тарифные планы"
          description="Выберите план, который подходит вам"
        />

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {plans.map((plan) => (
            <div
              key={plan.name}
              className={cn(
                'relative p-6 rounded-xl border transition-all',
                plan.current 
                  ? 'border-mv-accent bg-mv-accent/5' 
                  : plan.popular 
                    ? 'border-mv-accent/50 hover:border-mv-accent' 
                    : 'border-mv-border hover:border-mv-text-muted'
              )}
            >
              {plan.popular && (
                <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 bg-mv-accent text-white text-xs font-medium rounded-full">
                  Популярный
                </div>
              )}
              
              <h3 className="text-lg font-semibold text-mv-text">{plan.name}</h3>
              
              <div className="mt-4">
                <span className="text-3xl font-bold text-mv-text">{plan.price}</span>
                {plan.period && <span className="text-mv-text-muted">{plan.period}</span>}
              </div>
              
              <ul className="mt-6 space-y-3">
                {plan.features.map((feature, i) => (
                  <li key={i} className="flex items-center gap-2 text-sm">
                    <div className="w-4 h-4 rounded-full bg-mv-accent/10 flex items-center justify-center">
                      <div className="w-1.5 h-1.5 rounded-full bg-mv-accent" />
                    </div>
                    <span className="text-mv-text-secondary">{feature}</span>
                  </li>
                ))}
              </ul>
              
              <Button
                fullWidth
                variant={plan.current ? 'secondary' : 'primary'}
                className="mt-6"
                disabled={plan.current}
              >
                {plan.cta}
              </Button>
            </div>
          ))}
        </div>
      </Card>

      {/* API Documentation Quick Links */}
      <Card>
        <CardHeader
          title="Документация"
          description="Быстрый старт с API ЯВЬ"
        />

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <a
            href="/docs#api"
            className="flex items-center gap-3 p-4 rounded-lg bg-mv-surface-2 border border-mv-border hover:border-mv-accent transition-colors group"
          >
            <div className="w-10 h-10 rounded-lg bg-mv-accent/10 flex items-center justify-center group-hover:bg-mv-accent/20 transition-colors">
              <Code className="w-5 h-5 text-mv-accent" />
            </div>
            <div>
              <h4 className="font-medium text-mv-text">Примеры кода</h4>
              <p className="text-xs text-mv-text-muted">Python, JavaScript, cURL</p>
            </div>
            <ExternalLink className="w-4 h-4 text-mv-text-muted ml-auto" />
          </a>

          <a
            href="/docs#rate-limits"
            className="flex items-center gap-3 p-4 rounded-lg bg-mv-surface-2 border border-mv-border hover:border-mv-accent transition-colors group"
          >
            <div className="w-10 h-10 rounded-lg bg-mv-accent/10 flex items-center justify-center group-hover:bg-mv-accent/20 transition-colors">
              <Zap className="w-5 h-5 text-mv-accent" />
            </div>
            <div>
              <h4 className="font-medium text-mv-text">Rate Limits</h4>
              <p className="text-xs text-mv-text-muted">Лимиты и throttling</p>
            </div>
            <ExternalLink className="w-4 h-4 text-mv-text-muted ml-auto" />
          </a>

          <a
            href="/docs#errors"
            className="flex items-center gap-3 p-4 rounded-lg bg-mv-surface-2 border border-mv-border hover:border-mv-accent transition-colors group"
          >
            <div className="w-10 h-10 rounded-lg bg-mv-accent/10 flex items-center justify-center group-hover:bg-mv-accent/20 transition-colors">
              <Shield className="w-5 h-5 text-mv-accent" />
            </div>
            <div>
              <h4 className="font-medium text-mv-text">Обработка ошибок</h4>
              <p className="text-xs text-mv-text-muted">Коды и описания</p>
            </div>
            <ExternalLink className="w-4 h-4 text-mv-text-muted ml-auto" />
          </a>
        </div>
      </Card>

      {/* Usage Stats (placeholder) */}
      <Card>
        <CardHeader
          title="Использование API"
          description="Статистика запросов за текущий месяц"
        />

        <div className="flex items-center justify-center py-12 text-center">
          <div>
            <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-mv-surface-2 flex items-center justify-center">
              <Key className="w-8 h-8 text-mv-text-muted" />
            </div>
            <p className="text-mv-text-secondary mb-2">
              API статистика доступна на плане Pro и выше
            </p>
            <p className="text-sm text-mv-text-muted">
              Обновите план для доступа к детальной аналитике
            </p>
          </div>
        </div>
      </Card>
    </div>
  );
}
