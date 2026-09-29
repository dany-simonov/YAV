import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AppwriteException } from 'appwrite';

const functionsMock = vi.hoisted(() => ({
  createExecution: vi.fn(),
}));

vi.mock('./appwrite', () => ({
  APPWRITE_CONFIG: { functions: { analyze: 'analyze-function' } },
  functions: functionsMock,
}));

import {
  clearChecksHistory,
  deleteCheckFromHistory,
  loadCheckFromHistory,
  loadChecksHistory,
  mapHistoryRow,
} from './checkHistory';

const row = (overrides: Record<string, unknown> = {}) => ({
  check_id: 'check-1',
  user_id: 'user-1',
  media_type: 'text',
  status: 'completed',
  verdict: 'REAL',
  provider: '',
  model: 'sapling',
  ai_probability: null,
  decision_confidence: null,
  authenticity_index: 81,
  processing_ms: 120,
  source_label: 'Материал',
  created_at: '2026-08-08T12:00:00.000Z',
  explanation: 'Сохранённое пояснение',
  ...overrides,
});

const execution = (body: unknown, responseStatusCode = 200) => ({
  responseBody: JSON.stringify(body),
  responseStatusCode,
});

const page = (checks: Record<string, unknown>[], nextCursor: string | null = null) => execution({
  checks,
  next_cursor: nextCursor,
  page_size: 100,
});

const payloads = () => functionsMock.createExecution.mock.calls.map(
  ([request]) => JSON.parse(request.body as string),
);

describe('Function-backed check history', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('maps a summary DTO to the existing UI contract', () => {
    expect(mapHistoryRow(row())).toEqual({
      id: 'check-1',
      media_type: 'text',
      verdict: 'REAL',
      confidence: 81,
      authenticity_index: 81,
      model_used: 'sapling',
      explanation: 'Сохранённое пояснение',
      processing_ms: 120,
      created_at: '2026-08-08T12:00:00.000Z',
    });
  });

  it('parses full persisted Complex details only from the detail DTO', () => {
    const check = mapHistoryRow(row({
      verdict: 'FAKE',
      authenticity_index: 0,
      processing_ms: 0,
      details: JSON.stringify({
        analysis_mode: 'complex',
        ai_confidence: 0,
        short_report: 'Итог.',
        ai_details: {
          signals: [{ type: 'GENERIC_FORMULATION', severity: 'LOW', title: 'Шаблон', explanation: 'Пояснение.' }],
          human_signals: ['Авторская деталь.'],
        },
        credibility: {
          status: 'completed', credibility_index: 0, verdict: 'VERY_LOW_CREDIBILITY', confidence: 0,
          processing_ms: 0, summary: 'Недостаточно оснований.', issues: [], credible_points: [], sources: [],
        },
      }),
    }) as never);

    expect(check).toMatchObject({
      analysis_mode: 'complex',
      authenticity_index: 0,
      confidence: 0,
      short_report: 'Итог.',
      ai_details: { human_signals: ['Авторская деталь.'] },
      credibility: { credibility_index: 0, confidence: 0, processing_ms: 0 },
    });
  });

  it('lists history through the Function without sending userId and forwards the opaque cursor', async () => {
    functionsMock.createExecution
      .mockResolvedValueOnce(page([row()], 'cursor-1'))
      .mockResolvedValueOnce(page([row({ check_id: 'check-2' })]));

    const checks = await loadChecksHistory();

    expect(checks.map((check) => check.id)).toEqual(['check-1', 'check-2']);
    expect(functionsMock.createExecution).toHaveBeenCalledTimes(2);
    expect(payloads()).toEqual([
      { action: 'list_my_history', pageSize: 100 },
      { action: 'list_my_history', pageSize: 100, cursorAfter: 'cursor-1' },
    ]);
    expect(functionsMock.createExecution.mock.calls[0]?.[0]).toMatchObject({
      functionId: 'analyze-function',
    });
    expect(JSON.stringify(payloads())).not.toContain('userId');
  });

  it('returns an empty history from an empty Function page', async () => {
    functionsMock.createExecution.mockResolvedValueOnce(page([]));

    await expect(loadChecksHistory()).resolves.toEqual([]);
  });

  it('uses get_my_check and preserves detail explanation and details', async () => {
    functionsMock.createExecution.mockResolvedValueOnce(execution(row({
      details: JSON.stringify({ analysis_mode: 'complex', ai_confidence: 0.96 }),
    })));

    const check = await loadCheckFromHistory('check-1');

    expect(payloads()).toEqual([{ action: 'get_my_check', checkId: 'check-1' }]);
    expect(check.explanation).toBe('Сохранённое пояснение');
    expect(check.analysis_mode).toBe('complex');
    expect(check.confidence).toBe(0.96);
  });

  it('maps malformed legacy detail JSON to an optional empty report without failing the detail', async () => {
    functionsMock.createExecution.mockResolvedValueOnce(execution(row({ details: 'not-json' })));

    await expect(loadCheckFromHistory('check-1')).resolves.toMatchObject({
      id: 'check-1',
      explanation: 'Сохранённое пояснение',
      analysis_mode: undefined,
    });
  });

  it('deletes through delete_my_check without a client ownership check', async () => {
    functionsMock.createExecution.mockResolvedValueOnce(execution({ check_id: 'check-1', deleted: true }));

    await deleteCheckFromHistory('check-1');

    expect(payloads()).toEqual([{ action: 'delete_my_check', checkId: 'check-1' }]);
  });

  it('maps not-found without exposing a row or backend response', async () => {
    functionsMock.createExecution.mockResolvedValueOnce(execution({
      code: 'check_not_found', detail: 'Проверка не найдена.',
    }, 404));

    await expect(loadCheckFromHistory('foreign-check')).rejects.toThrow('Проверка не найдена');
    expect(JSON.stringify(payloads())).not.toContain('userId');
  });

  it('maps history_unavailable to a controlled user-facing error', async () => {
    functionsMock.createExecution.mockResolvedValueOnce(execution({
      code: 'history_unavailable', detail: 'internal Appwrite response',
    }, 503));

    await expect(loadChecksHistory()).rejects.toThrow('История проверок временно недоступна');
  });

  it('clears history by repeatedly loading the first page and never reusing a deleted cursor', async () => {
    functionsMock.createExecution
      .mockResolvedValueOnce(page([row(), row({ check_id: 'check-2' })], 'ignored-cursor'))
      .mockResolvedValueOnce(execution({ check_id: 'check-1', deleted: true }))
      .mockResolvedValueOnce(execution({ check_id: 'check-2', deleted: true }))
      .mockResolvedValueOnce(page([]));

    await clearChecksHistory();

    expect(payloads()).toEqual([
      { action: 'list_my_history', pageSize: 100 },
      { action: 'delete_my_check', checkId: 'check-1' },
      { action: 'delete_my_check', checkId: 'check-2' },
      { action: 'list_my_history', pageSize: 100 },
    ]);
  });

  it('stops clear on a partial delete failure instead of looping', async () => {
    functionsMock.createExecution
      .mockResolvedValueOnce(page([row()]))
      .mockResolvedValueOnce(execution({ code: 'history_unavailable', detail: 'unavailable' }, 503));

    await expect(clearChecksHistory()).rejects.toThrow('История проверок временно недоступна');
    expect(functionsMock.createExecution).toHaveBeenCalledTimes(2);
  });

  it('does not expose raw Appwrite transport errors', async () => {
    functionsMock.createExecution.mockRejectedValueOnce(
      new AppwriteException('response contained secret-token', 500, 'internal_error'),
    );

    await expect(loadChecksHistory()).rejects.toThrow('Не удалось загрузить историю проверок');
  });
});
