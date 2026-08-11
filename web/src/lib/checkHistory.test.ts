import { beforeEach, describe, expect, it, vi } from 'vitest';

const tablesDBMock = vi.hoisted(() => ({
  listRows: vi.fn(),
  getRow: vi.fn(),
  deleteRow: vi.fn(),
}));

vi.mock('./appwrite', () => ({
  APPWRITE_CONFIG: { databaseId: 'yav', tables: { checks: 'checks' } },
  tablesDB: tablesDBMock,
}));

import {
  clearChecksHistory,
  deleteCheckFromHistory,
  loadChecksHistory,
  mapHistoryRow,
} from './checkHistory';

const row = (overrides: Record<string, unknown> = {}) => ({
  $id: 'check-1',
  $createdAt: '2026-08-08T12:00:00.000Z',
  user_id: 'user-1',
  media_type: 'text',
  status: 'completed',
  verdict: 'REAL',
  authenticity_index: 81,
  model: 'sapling',
  source_label: 'Материал',
  processing_ms: 120,
  ...overrides,
});

describe('server-backed check history', () => {
  beforeEach(() => vi.clearAllMocks());

  it('maps a TablesDB row to the existing UI contract', () => {
    expect(mapHistoryRow(row() as never)).toEqual({
      id: 'check-1',
      media_type: 'text',
      verdict: 'REAL',
      confidence: 81,
      model_used: 'sapling',
      explanation: 'Материал',
      processing_ms: 120,
      created_at: '2026-08-08T12:00:00.000Z',
    });
  });

  it('returns only rows owned by the authenticated user', async () => {
    tablesDBMock.listRows.mockResolvedValue({
      rows: [row(), row({ $id: 'foreign', user_id: 'user-2' })],
      total: 2,
    });

    const checks = await loadChecksHistory('user-1');

    expect(checks.map((check) => check.id)).toEqual(['check-1']);
    expect(tablesDBMock.listRows).toHaveBeenCalledWith(
      expect.objectContaining({ databaseId: 'yav', tableId: 'checks' })
    );
  });

  it('loads more than the Appwrite default page in newest-first pages', async () => {
    const firstPage = Array.from({ length: 100 }, (_, index) =>
      row({ $id: `check-${index}` })
    );
    const secondPage = Array.from({ length: 30 }, (_, index) =>
      row({ $id: `check-${index + 100}` })
    );
    tablesDBMock.listRows
      .mockResolvedValueOnce({ rows: firstPage, total: 0 })
      .mockResolvedValueOnce({ rows: secondPage, total: 0 });

    const checks = await loadChecksHistory('user-1');

    expect(checks).toHaveLength(130);
    expect(tablesDBMock.listRows).toHaveBeenCalledTimes(2);
  });

  it('clears every page of the current user history', async () => {
    const firstPage = Array.from({ length: 100 }, (_, index) =>
      row({ $id: `check-${index}` })
    );
    const secondPage = Array.from({ length: 30 }, (_, index) =>
      row({ $id: `check-${index + 100}` })
    );
    tablesDBMock.listRows
      .mockResolvedValueOnce({ rows: firstPage, total: 0 })
      .mockResolvedValueOnce({ rows: secondPage, total: 0 });
    tablesDBMock.deleteRow.mockResolvedValue({});

    await clearChecksHistory('user-1');

    expect(tablesDBMock.deleteRow).toHaveBeenCalledTimes(130);
  });

  it('refuses to delete a readable row belonging to another user', async () => {
    tablesDBMock.getRow.mockResolvedValue(row({ user_id: 'user-2' }));

    await expect(deleteCheckFromHistory('user-1', 'check-1')).rejects.toThrow(
      'Нельзя удалить чужую проверку'
    );
    expect(tablesDBMock.deleteRow).not.toHaveBeenCalled();
  });

  it('does not expose raw backend errors', async () => {
    tablesDBMock.listRows.mockRejectedValue(new Error('response contained secret-token'));

    await expect(loadChecksHistory('user-1')).rejects.toThrow(
      'Не удалось загрузить историю проверок'
    );
  });
});
