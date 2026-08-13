import { AppwriteException, Query, type Models } from 'appwrite';

import { APPWRITE_CONFIG, tablesDB } from './appwrite';
import type { Check, MediaType, Verdict } from '../types';
import { displayModelName } from './resultPresentation';

const MAX_ITEMS = 200;
const PAGE_SIZE = 100;
const HISTORY_FIELDS = [
  '$id',
  '$createdAt',
  'user_id',
  'media_type',
  'verdict',
  'authenticity_index',
  'provider',
  'model',
  'explanation',
  'source_label',
  'processing_ms',
];

interface CheckRow extends Models.Row {
  user_id: string;
  media_type: string;
  verdict: string;
  authenticity_index: number;
  provider?: string | null;
  model?: string | null;
  explanation?: string | null;
  source_label?: string | null;
  processing_ms?: number | null;
}

export interface HistoryStats {
  checksToday: number;
  totalChecks: number;
  averageIndex: number | null;
  checksThisWeek: number;
}

const asMediaType = (value: string): MediaType =>
  ['image', 'audio', 'video', 'text'].includes(value) ? (value as MediaType) : 'text';

const asVerdict = (value: string): Verdict =>
  ['REAL', 'FAKE', 'UNCERTAIN'].includes(value) ? (value as Verdict) : 'UNCERTAIN';

const clampIndex = (value: number): number => {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
};

export function mapHistoryRow(row: CheckRow): Check {
  return {
    id: row.$id,
    media_type: asMediaType(row.media_type),
    verdict: asVerdict(row.verdict),
    confidence: clampIndex(row.authenticity_index),
    authenticity_index: clampIndex(row.authenticity_index),
    model_used: displayModelName(row.model || row.provider || 'Unknown model'),
    explanation: row.source_label || row.explanation || 'Проверка',
    processing_ms: Number(row.processing_ms || 0),
    created_at: row.$createdAt,
  };
}

function historyError(error: unknown): Error {
  logHistoryDiagnostic(error);
  if (error instanceof AppwriteException) {
    if (error.code === 401 || error.code === 403) {
      return new Error('Нет доступа к истории проверок');
    }
    if (error.code === 404) {
      return new Error('Таблица истории проверок не найдена');
    }
  }
  return new Error('Не удалось загрузить историю проверок');
}

function logHistoryDiagnostic(error: unknown): void {
  if (!import.meta.env.DEV || !(error instanceof AppwriteException)) return;
  const safe = (value: unknown, limit: number): string => {
    if (typeof value !== 'string') return '';
    return value
      .replace(/[\r\n]/g, ' ')
      .replace(/(?:bearer\s+|authorization\s*[:=]\s*|x-appwrite-(?:jwt|key|session)\s*[:=]\s*|(?:api[_ -]?(?:key|secret)|jwt|session(?:id)?|token|secret)\s*[:=]\s*)\S+/gi, '[REDACTED]')
      .replace(/\b(?:api[_ -]?(?:key|secret)|jwt|session(?:id)?|token|secret)(?:[-_][A-Za-z0-9]+)+\b/gi, '[REDACTED]')
      .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g, '[REDACTED]')
      .replace(/(["'`])(?:(?!\1).){1,512}\1/g, '$1[REDACTED]$1')
      .slice(0, limit);
  };
  console.warn('check_history_appwrite_error', {
    code: typeof error.code === 'number' ? error.code : 0,
    type: safe(error.type, 80),
    message: safe(error.message, 240),
  });
}

export async function loadChecksHistory(userId: string): Promise<Check[]> {
  if (!userId) return [];

  try {
    const rows: CheckRow[] = [];
    let offset = 0;

    while (rows.length < MAX_ITEMS) {
      const limit = Math.min(PAGE_SIZE, MAX_ITEMS - rows.length);
      const response = await tablesDB.listRows<CheckRow>({
        databaseId: APPWRITE_CONFIG.databaseId,
        tableId: APPWRITE_CONFIG.tables.checks,
        queries: [
          Query.equal('user_id', [userId]),
          Query.orderDesc('$createdAt'),
          Query.limit(limit),
          Query.offset(offset),
          Query.select(HISTORY_FIELDS),
        ],
        total: false,
        ttl: 0,
      });
      rows.push(...response.rows.filter((row) => row.user_id === userId));
      offset += response.rows.length;
      if (response.rows.length < limit) break;
    }

    return rows.slice(0, MAX_ITEMS).map(mapHistoryRow);
  } catch (error) {
    throw historyError(error);
  }
}

export async function deleteCheckFromHistory(userId: string, checkId: string): Promise<void> {
  if (!userId || !checkId) return;
  try {
    const row = await tablesDB.getRow<CheckRow>({
      databaseId: APPWRITE_CONFIG.databaseId,
      tableId: APPWRITE_CONFIG.tables.checks,
      rowId: checkId,
    });
    if (row.user_id !== userId) {
      throw new Error('Нельзя удалить чужую проверку');
    }
    await tablesDB.deleteRow({
      databaseId: APPWRITE_CONFIG.databaseId,
      tableId: APPWRITE_CONFIG.tables.checks,
      rowId: checkId,
    });
  } catch (error) {
    if (error instanceof Error && error.message === 'Нельзя удалить чужую проверку') throw error;
    throw historyError(error);
  }
}

export async function clearChecksHistory(userId: string): Promise<void> {
  if (!userId) return;
  try {
    const checks = await loadChecksHistory(userId);
    await Promise.all(checks.map((check) => deleteCheckFromHistory(userId, check.id)));
  } catch (error) {
    throw historyError(error);
  }
}

const isSameLocalDay = (a: Date, b: Date): boolean =>
  a.getFullYear() === b.getFullYear() &&
  a.getMonth() === b.getMonth() &&
  a.getDate() === b.getDate();

const getWeekStart = (dateValue: Date): Date => {
  const date = new Date(dateValue);
  const day = date.getDay();
  date.setDate(date.getDate() - (day === 0 ? 6 : day - 1));
  date.setHours(0, 0, 0, 0);
  return date;
};

export function calculateHistoryStats(checks: Check[]): HistoryStats {
  const now = new Date();
  const weekStart = getWeekStart(now);
  const checksToday = checks.filter((item) => isSameLocalDay(new Date(item.created_at), now)).length;
  const checksThisWeek = checks.filter((item) => new Date(item.created_at) >= weekStart).length;
  const averageIndex = checks.length
    ? Math.round(checks.reduce((sum, item) => sum + item.confidence, 0) / checks.length)
    : null;
  return { checksToday, totalChecks: checks.length, averageIndex, checksThisWeek };
}

export async function getHistoryStats(userId: string): Promise<HistoryStats> {
  return calculateHistoryStats(await loadChecksHistory(userId));
}
