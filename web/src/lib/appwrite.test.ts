import { describe, expect, it } from 'vitest';

import { APPWRITE_CONFIG } from './appwrite';

describe('Appwrite resource configuration', () => {
  it('uses the current YAV infrastructure defaults', () => {
    expect(APPWRITE_CONFIG).toMatchObject({
      endpoint: 'https://fra.cloud.appwrite.io/v1',
      projectId: '6a67d79d000fcca992f3',
      databaseId: 'yav',
      tables: { users: 'users', checks: 'checks' },
      buckets: { uploads: 'uploads' },
      functions: { analyze: 'analyze' },
    });
  });
});
