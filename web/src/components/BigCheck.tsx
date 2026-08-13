import { useState, useCallback } from 'react';
import { useAnalyze } from '../hooks';
import { VerdictBadge } from './VerdictBadge';
import { ConfidenceGauge } from './ConfidenceGauge';
import { displayModelName } from '../lib/resultPresentation';
import type { AnalyzeResult, MediaType } from '../types';

interface FileWithResult {
  file: File;
  result?: AnalyzeResult;
  loading: boolean;
  error?: string;
}

const ACCEPTED_TYPES = {
  image: ['image/jpeg', 'image/png', 'image/webp'],
  audio: ['audio/ogg', 'audio/mpeg', 'audio/wav'],
  video: ['video/mp4', 'video/webm'],
};

const MAX_FILE_SIZE = 20 * 1024 * 1024; // 20MB

export function BigCheck() {
  const [files, setFiles] = useState<FileWithResult[]>([]);
  const [textInput, setTextInput] = useState('');
  const [textResult, setTextResult] = useState<AnalyzeResult | null>(null);
  const [textLoading, setTextLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'file' | 'text'>('file');
  const [dragOver, setDragOver] = useState(false);
  const { analyzeFile, analyzeText } = useAnalyze();

  const detectMediaType = (file: File): MediaType => {
    if (file.type.startsWith('image/')) return 'image';
    if (file.type.startsWith('audio/')) return 'audio';
    if (file.type.startsWith('video/')) return 'video';
    return 'text';
  };

  const validateFile = (file: File): string | null => {
    if (file.size > MAX_FILE_SIZE) {
      return 'Файл слишком большой (макс. 20 МБ)';
    }
    const allTypes = [...ACCEPTED_TYPES.image, ...ACCEPTED_TYPES.audio, ...ACCEPTED_TYPES.video];
    if (!allTypes.includes(file.type)) {
      return 'Неподдерживаемый формат файла';
    }
    return null;
  };

  const handleFiles = useCallback(async (newFiles: FileList | File[]) => {
    const fileArray = Array.from(newFiles).slice(0, 10 - files.length);
    
    const validatedFiles: FileWithResult[] = fileArray.map(file => {
      const error = validateFile(file);
      return { file, loading: !error, error: error || undefined };
    });

    setFiles(prev => [...prev, ...validatedFiles]);

    // Process each valid file
    for (let i = 0; i < validatedFiles.length; i++) {
      const fileItem = validatedFiles[i];
      if (fileItem.error) continue;

      try {
        const result = await analyzeFile(fileItem.file);
        setFiles(prev => prev.map(f => 
          f.file === fileItem.file 
            ? { ...f, result: result || undefined, loading: false }
            : f
        ));
      } catch {
        setFiles(prev => prev.map(f =>
          f.file === fileItem.file
            ? { ...f, error: 'Ошибка анализа', loading: false }
            : f
        ));
      }
    }
  }, [files.length, analyzeFile]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    handleFiles(e.dataTransfer.files);
  }, [handleFiles]);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  };

  const handleDragLeave = () => {
    setDragOver(false);
  };

  const handleTextSubmit = async () => {
    if (!textInput.trim()) return;
    
    setTextLoading(true);
    const result = await analyzeText(textInput);
    setTextResult(result);
    setTextLoading(false);
  };

  const removeFile = (index: number) => {
    setFiles(prev => prev.filter((_, i) => i !== index));
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} Б`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
  };

  const getMediaIcon = (type: MediaType) => {
    switch (type) {
      case 'image': return '🖼️';
      case 'audio': return '🎵';
      case 'video': return '🎬';
      default: return '📄';
    }
  };

  return (
    <div className="bg-mv-surface border border-mv-border rounded-xl p-6">
      {/* Tabs */}
      <div className="flex gap-2 mb-6">
        <button
          onClick={() => setActiveTab('file')}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
            activeTab === 'file'
              ? 'bg-mv-accent text-white'
              : 'bg-mv-surface-2 text-mv-text-secondary hover:text-mv-text'
          }`}
        >
          Файл
        </button>
        <button
          onClick={() => setActiveTab('text')}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
            activeTab === 'text'
              ? 'bg-mv-accent text-white'
              : 'bg-mv-surface-2 text-mv-text-secondary hover:text-mv-text'
          }`}
        >
          Текст
        </button>
      </div>

      {/* File Upload Tab */}
      {activeTab === 'file' && (
        <>
          {/* Drop Zone */}
          <div
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            className={`relative border-2 border-dashed rounded-xl p-12 text-center transition-all ${
              dragOver
                ? 'border-mv-accent bg-mv-accent/5'
                : 'border-mv-border hover:border-mv-accent/50'
            }`}
          >
            <input
              type="file"
              multiple
              accept={[...ACCEPTED_TYPES.image, ...ACCEPTED_TYPES.audio, ...ACCEPTED_TYPES.video].join(',')}
              onChange={(e) => e.target.files && handleFiles(e.target.files)}
              className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
            />
            <div className="text-4xl mb-4">📁</div>
            <p className="text-mv-text font-medium mb-2">
              Перетащите файлы сюда или нажмите для выбора
            </p>
            <p className="text-sm text-mv-text-secondary">
              Фото, аудио • до 10 файлов • макс. 20 МБ каждый
            </p>
            <p className="text-xs text-mv-uncertain mt-2">
              Видео — скоро
            </p>
          </div>

          {/* Results */}
          {files.length > 0 && (
            <div className="mt-6 space-y-4">
              <h3 className="text-lg font-semibold text-mv-text">Результаты</h3>
              {files.map((item, index) => (
                <div
                  key={index}
                  className="bg-mv-surface-2 border border-mv-border rounded-lg p-4"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex items-center gap-3 min-w-0">
                      <span className="text-2xl">{getMediaIcon(detectMediaType(item.file))}</span>
                      <div className="min-w-0">
                        <p className="text-mv-text font-medium truncate">{item.file.name}</p>
                        <p className="text-xs text-mv-text-secondary">
                          {formatFileSize(item.file.size)}
                        </p>
                      </div>
                    </div>
                    <button
                      onClick={() => removeFile(index)}
                      className="text-mv-text-muted hover:text-mv-fake transition-colors"
                    >
                      ✕
                    </button>
                  </div>

                  {item.loading && (
                    <div className="mt-4 flex items-center gap-2 text-mv-text-secondary">
                      <div className="w-4 h-4 border-2 border-mv-accent border-t-transparent rounded-full animate-spin" />
                      Анализируем...
                    </div>
                  )}

                  {item.error && (
                    <div className="mt-4 text-mv-fake text-sm">{item.error}</div>
                  )}

                  {item.result && (
                    <div className="mt-4 flex items-center gap-6">
                      <ConfidenceGauge
                        confidence={item.result.confidence}
                        authenticityIndex={item.result.authenticity_index}
                        verdict={item.result.verdict}
                        size={80}
                      />
                      <div>
                        <VerdictBadge verdict={item.result.verdict} />
                        <p className="mt-2 text-sm text-mv-text-secondary">
                          {item.result.explanation}
                        </p>
                        <p className="mt-1 text-xs text-mv-text-muted">
                          Модель: {displayModelName(item.result.model_used)} • {item.result.processing_ms}мс
                        </p>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* Text Tab */}
      {activeTab === 'text' && (
        <>
          <textarea
            value={textInput}
            onChange={(e) => setTextInput(e.target.value)}
            placeholder="Вставьте текст для проверки..."
            className="w-full h-40 p-4 bg-mv-surface-2 border border-mv-border rounded-lg text-mv-text placeholder:text-mv-text-muted resize-none focus:border-mv-accent focus:outline-none transition-colors"
          />
          <div className="flex items-center justify-between mt-4">
            <span className="text-sm text-mv-text-secondary">
              {textInput.length} символов
            </span>
            <button
              onClick={handleTextSubmit}
              disabled={!textInput.trim() || textLoading}
              className="px-6 py-2 bg-mv-accent text-white rounded-lg font-medium hover:bg-mv-accent-hover disabled:opacity-50 disabled:cursor-not-allowed transition-all"
            >
              {textLoading ? 'Анализируем...' : 'Проверить'}
            </button>
          </div>

          {textResult && (
            <div className="mt-6 bg-mv-surface-2 border border-mv-border rounded-lg p-4">
              <div className="flex items-center gap-6">
                <ConfidenceGauge
                  confidence={textResult.confidence}
                  authenticityIndex={textResult.authenticity_index}
                  verdict={textResult.verdict}
                  size={80}
                />
                <div>
                  <VerdictBadge verdict={textResult.verdict} />
                  <p className="mt-2 text-sm text-mv-text-secondary">
                    {textResult.explanation}
                  </p>
                  <p className="mt-1 text-xs text-mv-text-muted">
                    Модель: {displayModelName(textResult.model_used)} • {textResult.processing_ms}мс
                  </p>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
