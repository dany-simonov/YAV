/**
 * New Check Page
 * ==============
 * Страница для создания новой проверки с Drag & Drop и табами.
 */

import { useState, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { FileImage, FileText, Layers3, Link2, Send, Loader2 } from 'lucide-react';

import { Card, Button, Alert } from '../../components/ui';
import { FileDropzone, TextInput } from '../../components/upload';
import { CheckResultCard } from '../../components/CheckResultCard';
import { cn } from '../../lib/utils';
import { functions, storage, ID, APPWRITE_CONFIG } from '../../lib/appwrite';
import {
  AnalysisExecutionError,
  analysisErrorMessageFromUnknown,
  parseAnalysisBackendError,
} from '../../lib/analysisError';
import { useAuthStore } from '../../store';
import { displayModelName } from '../../lib/resultPresentation';
import type { UploadFile, TabType, CheckResult } from '../../types';

interface Tab {
  id: TabType;
  label: string;
  icon: React.ReactNode;
}

const tabs: Tab[] = [
  { id: 'media', label: 'Файл', icon: <FileImage className="w-4 h-4" /> },
  { id: 'text', label: 'Текст', icon: <FileText className="w-4 h-4" /> },
  { id: 'complex', label: 'Комплексная проверка', icon: <Layers3 className="w-4 h-4" /> },
];

export function NewCheckPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTab = (searchParams.get('tab') as TabType) || 'media';
  
  const [activeTab, setActiveTab] = useState<TabType>(initialTab);
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [text, setText] = useState('');
  const [complexFiles, setComplexFiles] = useState<UploadFile[]>([]);
  const [complexText, setComplexText] = useState('');
  const [articleUrl, setArticleUrl] = useState('');
  const [complexSubmitted, setComplexSubmitted] = useState(false);
  
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CheckResult | null>(null);
  
  const { user } = useAuthStore();

  const detectMediaType = (file: File): CheckResult['media_type'] => {
    if (file.type.startsWith('image/')) return 'image';
    if (file.type.startsWith('audio/')) return 'audio';
    if (file.type.startsWith('video/')) return 'video';
    return 'image';
  };

  const setFileStatus = (
    id: string,
    status: UploadFile['status'],
    progress: number,
    fileError?: string
  ) => {
    setFiles((prev) =>
      prev.map((f) => (f.id === id ? { ...f, status, progress, error: fileError } : f))
    );
  };

  const normalizeFunctionResult = (data: any, mediaType: CheckResult['media_type']): CheckResult => {
    const source = data?.result ?? data;
    const rawConfidence = Number(source?.confidence ?? 0);
    const canonicalIndex = typeof source?.authenticity_index === 'number'
      ? source.authenticity_index
      : undefined;
    return {
      verdict: source?.verdict ?? 'UNCERTAIN',
      confidence: rawConfidence,
      authenticity_index: canonicalIndex ?? null,
      model_used: displayModelName(source?.model_used ?? source?.model ?? 'Unknown model'),
      explanation: source?.explanation ?? source?.reason ?? 'Результат получен без пояснения',
      processing_ms: Number(source?.processing_ms ?? source?.processingTime ?? 0),
      media_type: source?.media_type ?? mediaType,
      short_report: typeof source?.short_report === 'string' ? source.short_report : undefined,
      credibility: source?.credibility && typeof source.credibility === 'object'
        ? source.credibility : undefined,
      ai_status: source?.ai_status === 'unavailable' ? 'unavailable' : 'completed',
    };
  };

  const resetState = () => {
    setError(null);
    setResult(null);
  };

  const handleTabChange = (tabId: TabType) => {
    setActiveTab(tabId);
    setSearchParams({ tab: tabId });
    resetState();
  };

  const handleFilesSelected = useCallback((newFiles: UploadFile[]) => {
    setFiles((prev) => {
      prev.forEach((file) => {
        if (file.preview) URL.revokeObjectURL(file.preview);
      });
      return newFiles.slice(0, 1);
    }); // Keep only one file and allow replacing the current one.
    resetState();
  }, []);

  const handleRemoveFile = useCallback((id: string) => {
    setFiles((prev) => {
      const file = prev.find((f) => f.id === id);
      if (file?.preview) URL.revokeObjectURL(file.preview);
      return prev.filter((f) => f.id !== id);
    });
  }, []);

  const handleTextChange = useCallback((value: string) => {
    setText(value);
    resetState();
  }, []);

  const canSubmit = activeTab === 'media' 
    ? files.length > 0 && files.every((f) => f.status !== 'uploading' && f.status !== 'analyzing')
    : activeTab === 'text' && text.trim().length >= 1 && text.length <= 10000;

  const handleSubmit = async () => {
    if (!canSubmit || !user) return;
    
    setIsAnalyzing(true);
    resetState();

    try {
      let execution;
      let mediaType: CheckResult['media_type'] = 'text';
      let uploadedFileId: string | null = null;

      if (activeTab === 'media' && files.length > 0) {
        const fileRef = files[0];
        const fileToUpload = fileRef.file;
        mediaType = detectMediaType(fileToUpload);

        setFileStatus(fileRef.id, 'uploading', 35);
        
        // 1. Upload to Storage
        const uploadedFile = await storage.createFile(
          APPWRITE_CONFIG.buckets.uploads,
          ID.unique(),
          fileToUpload
        );
        uploadedFileId = uploadedFile.$id;
        setFileStatus(fileRef.id, 'analyzing', 70);

        // 2. Call analyze function
        const payload = {
          fileId: uploadedFile.$id,
          userId: user.$id,
          username: user.name,
          firstName: user.name.split(' ')[0] || '',
          mediaType,
          sourceLabel: fileToUpload.name,
        };
        execution = await functions.createExecution(APPWRITE_CONFIG.functions.analyze, JSON.stringify(payload));
      } else if (activeTab === 'text') {
        mediaType = 'text';
        const payload = {
          text,
          userId: user.$id,
          username: user.name,
          firstName: user.name.split(' ')[0] || '',
          mediaType,
          sourceLabel: text.slice(0, 120).replace(/\s+/g, ' ').trim(),
        };
        execution = await functions.createExecution(APPWRITE_CONFIG.functions.analyze, JSON.stringify(payload));
      } else {
        throw new Error('Нет данных для анализа');
      }

      if (!execution.responseBody) {
        throw new Error('Функция не вернула ответ. Проверьте логи Appwrite Function.');
      }

      const resultData = JSON.parse(execution.responseBody);
      const backendError = parseAnalysisBackendError(resultData);
      if (backendError?.code === 'email_not_verified') {
        if (uploadedFileId) {
          try {
            await storage.deleteFile(APPWRITE_CONFIG.buckets.uploads, uploadedFileId);
          } catch {
            // Best effort cleanup before leaving the page.
          }
        }
        navigate('/verify-email', {
          replace: true,
          state: { notice: 'Подтвердите email перед запуском анализа.' },
        });
        return;
      }
      if (backendError || execution.responseStatusCode >= 400) {
        throw new AnalysisExecutionError(backendError);
      }

      const normalizedResult = normalizeFunctionResult(resultData, mediaType);
      setResult(normalizedResult);

      if (activeTab === 'media' && files.length > 0) {
        setFileStatus(files[0].id, 'complete', 100);
      }

      // Best effort cleanup after successful analyze.
      if (uploadedFileId) {
        try {
          await storage.deleteFile(APPWRITE_CONFIG.buckets.uploads, uploadedFileId);
        } catch {
          // Ignore cleanup errors.
        }
      }

    } catch (e) {
      const message = analysisErrorMessageFromUnknown(e);
      setError(message);
      if (activeTab === 'media' && files.length > 0) {
        setFileStatus(files[0].id, 'error', 0, message);
      }
    } finally {
      setIsAnalyzing(false);
    }
  };

  return (
    <div className="max-w-4xl space-y-7 pt-4 lg:pt-8">
      <div>
        <p className="eyebrow mb-4">Рабочая область</p>
        <h1 className="text-4xl lg:text-[42px] leading-none tracking-[-.045em] font-semibold text-mv-text">Новая проверка</h1>
        <p className="mt-4 text-mv-text-secondary">Выберите материал. Максимальный размер файла в текущем продукте — 20 МБ.</p>
      </div>

      <Card padding="none" variant="elevated" className="overflow-hidden !rounded-[20px]">
        <div className="flex px-4 pt-3 border-b border-mv-border">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => handleTabChange(tab.id)}
              className={cn(
                'flex items-center gap-2 px-5 py-3 text-sm font-medium transition-colors relative',
                activeTab === tab.id ? 'text-black' : 'text-mv-text-secondary hover:text-mv-text',
                isAnalyzing && 'pointer-events-none opacity-50'
              )}
            >
              {tab.icon}
              {tab.label}
              {activeTab === tab.id && <div className="absolute bottom-0 left-3 right-3 h-px bg-black" />}
            </button>
          ))}
        </div>

        <div className="p-4 sm:p-6">
          {activeTab === 'media' ? (
            <FileDropzone files={files} onFilesSelected={handleFilesSelected} onRemoveFile={handleRemoveFile} disabled={isAnalyzing} maxFiles={1} />
          ) : activeTab === 'text' ? (
            <TextInput value={text} onChange={handleTextChange} disabled={isAnalyzing} />
          ) : (
            <div className="space-y-5">
              <div className="rounded-2xl bg-black p-6 text-white sm:p-8"><p className="eyebrow !text-white/50">Мультимодальный материал</p><h2 className="mt-4 text-2xl font-semibold tracking-[-.04em]">Добавьте ссылку, текст и файл вместе</h2><p className="mt-3 max-w-2xl text-sm leading-6 text-white/65">Соберите контекст статьи в одной проверке: источник, фрагмент текста и изображение или другой медиафайл.</p></div>
              <label className="block"><span className="mb-2 flex items-center gap-2 text-sm font-semibold"><Link2 size={16} /> Ссылка на статью</span><input value={articleUrl} onChange={(event) => { setArticleUrl(event.target.value); setComplexSubmitted(false); }} type="url" placeholder="https://example.com/article" className="w-full rounded-xl border border-mv-border bg-white px-4 py-3 text-sm outline-none transition focus:border-black" /></label>
              <TextInput value={complexText} onChange={(value) => { setComplexText(value); setComplexSubmitted(false); }} disabled={false} />
              <FileDropzone files={complexFiles} onFilesSelected={(newFiles) => { setComplexFiles(newFiles.slice(0, 1)); setComplexSubmitted(false); }} onRemoveFile={(id) => setComplexFiles((current) => current.filter((file) => file.id !== id))} maxFiles={1} />
              <Button onClick={() => setComplexSubmitted(true)} disabled={!articleUrl.trim() && !complexText.trim() && complexFiles.length === 0} leftIcon={<Send className="w-4 h-4" />}>Собрать комплексную проверку</Button>
              {complexSubmitted && <div className="rounded-2xl border border-black/[.09] bg-[#fafaf9] p-6 sm:p-8"><p className="eyebrow">Состав проверки</p><h3 className="mt-3 text-2xl font-semibold tracking-[-.035em]">Материал собран</h3><div className="mt-6 grid gap-3 sm:grid-cols-3"><div className="rounded-xl bg-white p-4 text-sm"><p className="text-mv-text-muted">Ссылка</p><p className="mt-2 font-semibold">{articleUrl ? 'Добавлена' : 'Нет'}</p></div><div className="rounded-xl bg-white p-4 text-sm"><p className="text-mv-text-muted">Текст</p><p className="mt-2 font-semibold">{complexText ? `${complexText.length} символов` : 'Нет'}</p></div><div className="rounded-xl bg-white p-4 text-sm"><p className="text-mv-text-muted">Файл</p><p className="mt-2 truncate font-semibold">{complexFiles[0]?.file.name ?? 'Нет'}</p></div></div></div>}
            </div>
          )}
        </div>

        {activeTab !== 'complex' && (
          <div className="px-6 py-4 bg-[#fafaf9] border-t border-mv-border flex items-center justify-between">
            <p className="text-sm text-mv-text-muted">
              {activeTab === 'media' ? `${files.length} файл(ов) выбрано` : `${text.length} символов`}
            </p>
            <Button
              onClick={handleSubmit}
              disabled={!canSubmit || isAnalyzing}
              leftIcon={isAnalyzing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            >
              {isAnalyzing ? 'Анализ...' : 'Запустить проверку'}
            </Button>
          </div>
        )}
      </Card>
      
      {error && <Alert variant="error" title="Ошибка анализа">{error}</Alert>}
      {result && <CheckResultCard result={result} />}

      <p className="text-xs text-mv-text-muted max-w-xl leading-5">Материалы передаются по защищённому соединению и удаляются из временного хранилища после завершения анализа.</p>
    </div>
  );
}
