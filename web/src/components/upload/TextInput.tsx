/**
 * Text Input Component
 * ====================
 * Компонент для ввода текста для проверки.
 */

import { useState, type ChangeEvent } from 'react';
import { FileText, AlertCircle } from 'lucide-react';
import { cn } from '../../lib/utils';

interface TextInputProps {
  value: string;
  onChange: (value: string) => void;
  minLength?: number;
  maxLength?: number;
  disabled?: boolean;
  recommendedRange?: { min: number; max: number };
}

export function TextInput({
  value,
  onChange,
  minLength = 50,
  maxLength = 10000,
  disabled = false,
  recommendedRange,
}: TextInputProps) {
  const [isFocused, setIsFocused] = useState(false);
  
  const charCount = value.length;
  const isValid = charCount >= minLength;
  const isOverLimit = charCount > maxLength;
  
  const handleChange = (e: ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = e.target.value;
    if (newValue.length <= maxLength) {
      onChange(newValue);
    }
  };

  return (
    <div className="space-y-3">
      {/* Textarea Container */}
      <div
        className={cn(
          'relative border-2 rounded-xl transition-all duration-200',
          isFocused ? 'border-mv-accent' : 'border-mv-border',
          disabled && 'opacity-50'
        )}
      >
        <textarea
          value={value}
          onChange={handleChange}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          disabled={disabled}
          placeholder={`Вставьте текст для проверки...

Минимум ${minLength} символов для точного анализа. Чем больше текста, тем выше точность.`}
          className={cn(
            'w-full h-64 p-4 bg-transparent text-mv-text placeholder-mv-text-muted resize-none',
            'focus:outline-none',
            'disabled:cursor-not-allowed'
          )}
        />

        {/* Character Counter */}
        <div className="absolute bottom-3 right-3 flex items-center gap-2">
          <span
            className={cn(
              'text-xs font-medium',
              isOverLimit ? 'text-mv-fake' : charCount > 0 && isValid ? 'text-mv-real' : 'text-mv-text-muted'
            )}
          >
            {charCount.toLocaleString()} / {maxLength.toLocaleString()}
          </span>
        </div>
      </div>

      {/* Hints */}
      <div className="flex items-start gap-4 text-sm">
        {/* Minimum chars hint */}
        {charCount > 0 && !isValid && (
          <div className="flex items-center gap-2 text-mv-uncertain">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span>Минимум {minLength} символов (ещё {minLength - charCount})</span>
          </div>
        )}

        {/* Success hint */}
        {isValid && !isOverLimit && (
          <div className="flex items-center gap-2 text-mv-real">
            <FileText className="w-4 h-4 flex-shrink-0" />
            <span>Готово к проверке</span>
          </div>
        )}

        {/* Over limit warning */}
        {isOverLimit && (
          <div className="flex items-center gap-2 text-mv-fake">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span>Превышен лимит символов</span>
          </div>
        )}
      </div>

      {/* Minimal preparation hint */}
      <div className="pt-4 border-t border-black/[.08] grid sm:grid-cols-[170px_1fr] gap-2 sm:gap-6">
        <p className="eyebrow">Для точного анализа</p>
        <p className="text-sm text-mv-text-secondary">
          Полные абзацы · исходный текст ·{' '}
          {recommendedRange
            ? `${recommendedRange.min}–${recommendedRange.max} символов`
            : `от ${minLength} символов`}
        </p>
      </div>
    </div>
  );
}
