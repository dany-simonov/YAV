import { AppwriteException } from 'appwrite';

import { APPWRITE_CONFIG, functions } from './appwrite';
import type { AIOriginDetails, Check, CredibilityAssessment, MediaType, SourceAnalysisDetails, SourceMediaResult, Verdict } from '../types';
import { displayModelName } from './resultPresentation';

const MAX_ITEMS = 200;
const PAGE_SIZE = 100;
const MAX_CLEAR_BATCHES = 1_000;

export interface HistoryCheckSummary {
  check_id: string;
  user_id: string;
  media_type: string;
  status: string;
  verdict: string;
  provider: string;
  model: string;
  ai_probability: number | null;
  decision_confidence: number | null;
  authenticity_index: number | null;
  processing_ms: number | null;
  source_label: string;
  created_at: string;
  explanation: string;
}

export interface HistoryCheckDetail extends HistoryCheckSummary {
  details: string | null;
}

interface HistoryPageResponse {
  checks: HistoryCheckSummary[];
  next_cursor: string | null;
  page_size: number;
}

interface FunctionErrorPayload {
  code?: string;
  detail?: string;
}

class HistoryFunctionError extends Error {
  constructor(
    readonly status: number,
    readonly code?: string,
  ) {
    super(code || 'history_function_failed');
  }
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

const clampIndex = (value: number | null): number => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
};

export function mapHistoryRow(row: HistoryCheckSummary | HistoryCheckDetail): Check {
  const details = 'details' in row ? parseDetails(row.details) : {};
  const isComplex = details.analysis_mode === 'complex';
  return {
    id: row.check_id,
    media_type: asMediaType(row.media_type),
    verdict: asVerdict(row.verdict),
    // Complex confidence is not a score and must never be reconstructed from
    // authenticity_index. Current backend persistence may omit it; retain null
    // in that case rather than inventing a value.
    confidence: isComplex ? details.ai_confidence ?? null : clampIndex(row.authenticity_index),
    authenticity_index: clampIndex(row.authenticity_index),
    model_used: displayModelName(row.model || row.provider || 'Unknown model'),
    explanation: row.explanation || row.source_label || 'Проверка',
    processing_ms: Number(row.processing_ms || 0),
    created_at: row.created_at,
    short_report: details.short_report,
    credibility: details.credibility,
    ai_status: details.ai_status,
    analysis_mode: details.analysis_mode,
    ai_details: details.ai_details,
    source: details.source,
    complex_media: details.complex_media,
  };
}

function parseDetails(value: string | null | undefined): {
  short_report?: string;
  credibility?: CredibilityAssessment;
  ai_status?: 'completed' | 'unavailable';
  analysis_mode?: 'complex';
  ai_details?: AIOriginDetails;
  ai_confidence?: number;
  source?: SourceAnalysisDetails;
  complex_media?: SourceMediaResult[];
} {
  if (typeof value !== 'string' || value.length > 16_384) return {};
  try {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || typeof parsed !== 'object') return {};
    const item = parsed as Record<string, unknown>;
    const credibility = item.credibility;
    return {
      short_report: typeof item.short_report === 'string' ? item.short_report : undefined,
      credibility: isCredibilityAssessment(credibility) ? credibility : undefined,
      ai_status: item.ai_status === 'unavailable' ? 'unavailable' : undefined,
      analysis_mode: item.analysis_mode === 'complex' ? 'complex' : undefined,
      ai_details: isAIOriginDetails(item.ai_details) ? item.ai_details : undefined,
      ai_confidence: isUnitConfidence(item.ai_confidence) ? item.ai_confidence : undefined,
      source: isSourceAnalysisDetails(item.source) ? item.source : undefined,
      complex_media: Array.isArray(item.complex_media) ? item.complex_media as SourceMediaResult[] : undefined,
    };
  } catch {
    return {};
  }
}

function isUnitConfidence(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
}

function isAIOriginDetails(value: unknown): value is AIOriginDetails {
  if (!value || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  return Array.isArray(item.signals) && Array.isArray(item.human_signals);
}

function isSourceAnalysisDetails(value: unknown): value is SourceAnalysisDetails {
  if (!value || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  return typeof item.url === 'string' && typeof item.title === 'string'
    && typeof item.description === 'string' && typeof item.site_name === 'string'
    && typeof item.text_found === 'boolean' && typeof item.text_truncated === 'boolean'
    && typeof item.images_analyzed === 'number' && typeof item.video_analyzed === 'boolean'
    && Array.isArray(item.media);
}

function isCredibilityAssessment(value: unknown): value is CredibilityAssessment {
  if (!value || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  return (item.status === 'completed' || item.status === 'unavailable')
    && typeof item.summary === 'string'
    && Array.isArray(item.issues)
    && Array.isArray(item.sources)
    && (item.model === undefined || (typeof item.model === 'string' && item.model.length > 0))
    && (item.processing_ms === undefined || (
      typeof item.processing_ms === 'number'
      && Number.isInteger(item.processing_ms)
      && item.processing_ms >= 0
      && item.processing_ms <= 60_000
    ));
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function asNullableNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function parseHistorySummary(value: unknown): HistoryCheckSummary | null {
  const row = asRecord(value);
  if (!row) return null;
  const stringFields = [
    'check_id', 'user_id', 'media_type', 'status', 'verdict', 'provider', 'model',
    'source_label', 'created_at', 'explanation',
  ] as const;
  if (stringFields.some((field) => typeof row[field] !== 'string')) return null;
  return {
    check_id: row.check_id as string,
    user_id: row.user_id as string,
    media_type: row.media_type as string,
    status: row.status as string,
    verdict: row.verdict as string,
    provider: row.provider as string,
    model: row.model as string,
    ai_probability: asNullableNumber(row.ai_probability),
    decision_confidence: asNullableNumber(row.decision_confidence),
    authenticity_index: asNullableNumber(row.authenticity_index),
    processing_ms: asNullableNumber(row.processing_ms),
    source_label: row.source_label as string,
    created_at: row.created_at as string,
    explanation: row.explanation as string,
  };
}

function parseHistoryDetail(value: unknown): HistoryCheckDetail | null {
  const row = asRecord(value);
  const summary = parseHistorySummary(value);
  if (!row || !summary || (row.details !== null && typeof row.details !== 'string')) {
    return null;
  }
  return { ...summary, details: row.details as string | null };
}

function responseError(value: unknown): FunctionErrorPayload {
  const body = asRecord(value);
  return {
    code: typeof body?.code === 'string' ? body.code : undefined,
    detail: typeof body?.detail === 'string' ? body.detail : undefined,
  };
}

async function invokeHistoryFunction(payload: Record<string, unknown>): Promise<unknown> {
  let execution;
  try {
    execution = await functions.createExecution({
      functionId: APPWRITE_CONFIG.functions.analyze,
      body: JSON.stringify(payload),
    });
  } catch (error) {
    logHistoryDiagnostic(error);
    throw error;
  }

  let body: unknown;
  try {
    body = execution.responseBody ? JSON.parse(execution.responseBody) : null;
  } catch {
    throw new HistoryFunctionError(execution.responseStatusCode || 502);
  }
  const error = responseError(body);
  if (execution.responseStatusCode >= 400 || error.code || error.detail) {
    throw new HistoryFunctionError(execution.responseStatusCode, error.code);
  }
  return body;
}

function parseHistoryPage(value: unknown): HistoryPageResponse | null {
  const response = asRecord(value);
  if (!response || !Array.isArray(response.checks)
    || (response.next_cursor !== null && typeof response.next_cursor !== 'string')
    || !Number.isInteger(response.page_size)) {
    return null;
  }
  const checks = response.checks.map(parseHistorySummary);
  return checks.every((check): check is HistoryCheckSummary => check !== null)
    ? { checks, next_cursor: response.next_cursor as string | null, page_size: response.page_size as number }
    : null;
}

async function loadHistoryPage(cursorAfter?: string): Promise<HistoryPageResponse> {
  const response = parseHistoryPage(await invokeHistoryFunction({
    action: 'list_my_history',
    pageSize: PAGE_SIZE,
    ...(cursorAfter ? { cursorAfter } : {}),
  }));
  if (!response) throw new HistoryFunctionError(502);
  return response;
}

function historyError(error: unknown): Error {
  if (error instanceof HistoryFunctionError) {
    if (error.code === 'check_not_found') return new Error('Проверка не найдена');
    if (error.code === 'history_unavailable') return new Error('История проверок временно недоступна');
    if (error.code === 'email_not_verified') return new Error('Подтвердите email для доступа к истории проверок');
    if (error.code === 'authentication_required' || error.status === 401 || error.status === 403) {
      return new Error('Нет доступа к истории проверок');
    }
  }
  if (error instanceof AppwriteException && (error.code === 401 || error.code === 403)) {
    return new Error('Нет доступа к истории проверок');
  }
  return new Error('Не удалось загрузить историю проверок');
}

function logHistoryDiagnostic(error: unknown): void {
  if (!import.meta.env.DEV) return;
  if (error instanceof HistoryFunctionError) {
    console.warn('check_history_function_error', { status: error.status, code: error.code || '' });
    return;
  }
  if (!(error instanceof AppwriteException)) return;
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

export async function loadChecksHistory(): Promise<Check[]> {
  try {
    const rows: HistoryCheckSummary[] = [];
    const seenCursors = new Set<string>();
    let cursorAfter: string | undefined;

    while (rows.length < MAX_ITEMS) {
      const response = await loadHistoryPage(cursorAfter);
      rows.push(...response.checks.slice(0, MAX_ITEMS - rows.length));
      if (!response.next_cursor || response.checks.length === 0 || rows.length >= MAX_ITEMS) break;
      if (seenCursors.has(response.next_cursor)) throw new HistoryFunctionError(502);
      seenCursors.add(response.next_cursor);
      cursorAfter = response.next_cursor;
    }
    return rows.map(mapHistoryRow);
  } catch (error) {
    throw historyError(error);
  }
}

export async function loadCheckFromHistory(checkId: string): Promise<Check> {
  if (!checkId) throw new Error('Проверка не найдена');
  try {
    const detail = parseHistoryDetail(await invokeHistoryFunction({
      action: 'get_my_check',
      checkId,
    }));
    if (!detail) throw new HistoryFunctionError(502);
    return mapHistoryRow(detail);
  } catch (error) {
    throw historyError(error);
  }
}

async function deleteHistoryCheck(checkId: string): Promise<void> {
  const response = asRecord(await invokeHistoryFunction({
    action: 'delete_my_check',
    checkId,
  }));
  if (!response || response.check_id !== checkId || response.deleted !== true) {
    throw new HistoryFunctionError(502);
  }
}

export async function deleteCheckFromHistory(checkId: string): Promise<void> {
  if (!checkId) return;
  try {
    await deleteHistoryCheck(checkId);
  } catch (error) {
    throw historyError(error);
  }
}

export async function clearChecksHistory(): Promise<void> {
  try {
    for (let batch = 0; batch < MAX_CLEAR_BATCHES; batch += 1) {
      // Always restart from the first page after deletion. A cursor for a row
      // just deleted is intentionally never sent back to Appwrite.
      const response = await loadHistoryPage();
      if (response.checks.length === 0) return;
      for (const check of response.checks) {
        await deleteHistoryCheck(check.check_id);
      }
    }
    throw new HistoryFunctionError(502);
  } catch (error) {
    throw historyError(error);
  }
}

const isSameLocalDay = (a: Date, b: Date): boolean =>
  a.getFullYear() === b.getFullYear()
  && a.getMonth() === b.getMonth()
  && a.getDate() === b.getDate();

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
    ? Math.round(checks.reduce((sum, item) => sum + (item.authenticity_index ?? 0), 0) / checks.length)
    : null;
  return { checksToday, totalChecks: checks.length, averageIndex, checksThisWeek };
}

export async function getHistoryStats(): Promise<HistoryStats> {
  return calculateHistoryStats(await loadChecksHistory());
}
