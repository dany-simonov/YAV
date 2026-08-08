/**
 * File Dropzone Component
 * =======================
 * Красивая зона для drag-and-drop загрузки файлов.
 */

import { useCallback, useState } from 'react';
import { useDropzone, type FileRejection } from 'react-dropzone';
import { Upload, X, FileImage, FileAudio, FileVideo, File, AlertCircle } from 'lucide-react';
import { cn, formatFileSize } from '../../lib/utils';
import type { UploadFile } from '../../types';

interface FileDropzoneProps {
  onFilesSelected: (files: UploadFile[]) => void;
  files: UploadFile[];
  onRemoveFile: (id: string) => void;
  maxFiles?: number;
  maxSize?: number; // in bytes
  accept?: Record<string, string[]>;
  disabled?: boolean;
}

const DEFAULT_ACCEPT = {
  'image/*': ['.jpg', '.jpeg', '.png', '.webp', '.gif'],
  'audio/*': ['.mp3', '.wav', '.ogg', '.m4a', '.flac'],
  'video/*': ['.mp4', '.webm', '.mov', '.avi'],
};

const DEFAULT_MAX_SIZE = 20 * 1024 * 1024; // 20MB

export function FileDropzone({
  onFilesSelected,
  files,
  onRemoveFile,
  maxFiles = 10,
  maxSize = DEFAULT_MAX_SIZE,
  accept = DEFAULT_ACCEPT,
  disabled = false,
}: FileDropzoneProps) {
  const [errors, setErrors] = useState<string[]>([]);

  const onDrop = useCallback(
    (acceptedFiles: File[], rejectedFiles: FileRejection[]) => {
      setErrors([]);

      // Handle rejected files
      if (rejectedFiles.length > 0) {
        const newErrors: string[] = [];
        rejectedFiles.forEach((rejection) => {
          rejection.errors.forEach((error) => {
            if (error.code === 'file-too-large') {
              newErrors.push(`${rejection.file.name}: Файл слишком большой (макс. ${formatFileSize(maxSize)})`);
            } else if (error.code === 'file-invalid-type') {
              newErrors.push(`${rejection.file.name}: Неподдерживаемый формат`);
            } else if (error.code === 'too-many-files') {
              newErrors.push(`Максимум ${maxFiles} файлов`);
            }
          });
        });
        setErrors(newErrors);
      }

      // Convert accepted files to UploadFile format
      if (acceptedFiles.length > 0) {
        const newFiles: UploadFile[] = acceptedFiles.map((file) => ({
          id: `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          file,
          preview: file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined,
          progress: 0,
          status: 'pending',
        }));
        onFilesSelected(newFiles);
      }
    },
    [onFilesSelected, maxSize, maxFiles]
  );

  const { getRootProps, getInputProps, isDragActive, isDragReject } = useDropzone({
    onDrop,
    accept,
    maxSize,
    maxFiles: maxFiles - files.length,
    disabled: disabled || files.length >= maxFiles,
  });

  // Get icon based on file type
  const getFileIcon = (file: File) => {
    if (file.type.startsWith('image/')) return <FileImage className="w-5 h-5" />;
    if (file.type.startsWith('audio/')) return <FileAudio className="w-5 h-5" />;
    if (file.type.startsWith('video/')) return <FileVideo className="w-5 h-5" />;
    return <File className="w-5 h-5" />;
  };

  // Get status color
  const getStatusClass = (status: UploadFile['status']) => {
    switch (status) {
      case 'uploading':
      case 'analyzing':
        return 'border-mv-accent';
      case 'complete':
        return 'border-mv-real';
      case 'error':
        return 'border-mv-fake';
      default:
        return 'border-mv-border';
    }
  };

  return (
    <div className="space-y-4">
      {/* Dropzone Area */}
      <div
        {...getRootProps()}
        className={cn(
          'relative border border-black/[.1] rounded-[14px] p-10 lg:p-16 min-h-[430px] transition-all duration-200 cursor-pointer shadow-[0_1px_2px_rgba(0,0,0,.035),0_12px_30px_rgba(0,0,0,.055)]',
          'flex flex-col items-center justify-center text-center',
          isDragActive && !isDragReject && 'border-mv-accent bg-black/[.025]',
          isDragReject && 'border-mv-fake bg-mv-fake/5',
          !isDragActive && !disabled && 'border-black/15 hover:border-black/35 hover:bg-mv-surface-2/30',
          disabled && 'opacity-50 cursor-not-allowed',
          files.length >= maxFiles && 'opacity-50 cursor-not-allowed'
        )}
      >
        <input {...getInputProps()} />
        
        {/* Cloud Icon */}
        <div
          className={cn(
            'w-12 h-12 rounded-xl flex items-center justify-center mb-5 transition-colors',
            isDragActive && !isDragReject ? 'bg-black text-white' : 'bg-mv-surface-2',
            isDragReject && 'bg-mv-fake/20'
          )}
        >
          <Upload
            className={cn(
              'w-5 h-5 transition-colors',
              isDragActive && !isDragReject ? 'text-mv-accent' : 'text-mv-text-muted',
              isDragReject && 'text-mv-fake'
            )}
          />
        </div>

        {/* Text */}
        <div className="space-y-2">
          {isDragActive ? (
            isDragReject ? (
              <p className="text-mv-fake font-medium">Неподдерживаемый формат файла</p>
            ) : (
              <p className="text-mv-accent font-medium">Отпустите файлы для загрузки</p>
            )
          ) : (
            <>
              <p className="text-xl sm:text-2xl tracking-[-.03em] text-mv-text font-semibold">
                Перетащите файл или выберите на устройстве
              </p>
              <p className="mt-4 text-sm sm:text-base text-mv-text-muted">
                Изображения и аудио · до {formatFileSize(maxSize)}
              </p>
            </>
          )}
        </div>

        <div className="mt-2 text-sm sm:text-base text-mv-text-muted">Поддержка видео требует подтверждения</div>
        <button type="button" className="btn-light !min-h-[42px] mt-6 pointer-events-none">Выбрать файл</button>
      </div>

      {/* Errors */}
      {errors.length > 0 && (
        <div className="space-y-2">
          {errors.map((error, index) => (
            <div
              key={index}
              className="flex items-center gap-2 p-3 bg-mv-fake/10 border border-mv-fake/20 rounded-lg text-sm text-mv-fake"
            >
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              {error}
            </div>
          ))}
        </div>
      )}

      {/* File List */}
      {files.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm font-medium text-mv-text-secondary">
            Выбрано файлов: {files.length} / {maxFiles}
          </p>
          <div className="grid gap-2">
            {files.map((uploadFile) => (
              <div
                key={uploadFile.id}
                className={cn(
                  'flex items-center gap-3 p-3 bg-mv-surface rounded-lg border transition-colors',
                  getStatusClass(uploadFile.status)
                )}
              >
                {/* Preview or Icon */}
                {uploadFile.preview ? (
                  <img
                    src={uploadFile.preview}
                    alt=""
                    className="w-10 h-10 rounded object-cover flex-shrink-0"
                  />
                ) : (
                  <div className="w-10 h-10 rounded bg-mv-surface-2 flex items-center justify-center flex-shrink-0 text-mv-text-muted">
                    {getFileIcon(uploadFile.file)}
                  </div>
                )}

                {/* File Info */}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-mv-text truncate">
                    {uploadFile.file.name}
                  </p>
                  <p className="text-xs text-mv-text-muted">
                    {formatFileSize(uploadFile.file.size)}
                    {uploadFile.status === 'uploading' && ` • Загрузка ${uploadFile.progress}%`}
                    {uploadFile.status === 'analyzing' && ' • Анализ...'}
                    {uploadFile.status === 'complete' && ' • Готово'}
                    {uploadFile.status === 'error' && ` • ${uploadFile.error || 'Ошибка'}`}
                  </p>
                </div>

                {/* Progress Bar */}
                {(uploadFile.status === 'uploading' || uploadFile.status === 'analyzing') && (
                  <div className="w-20 h-1.5 bg-mv-surface-2 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-mv-accent rounded-full transition-all duration-300"
                      style={{ width: `${uploadFile.progress}%` }}
                    />
                  </div>
                )}

                {/* Remove Button */}
                {uploadFile.status !== 'uploading' && uploadFile.status !== 'analyzing' && !disabled && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onRemoveFile(uploadFile.id);
                    }}
                    className="p-1.5 rounded-md text-mv-text-muted hover:text-mv-fake hover:bg-mv-fake/10 transition-colors"
                  >
                    <X className="w-4 h-4" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
