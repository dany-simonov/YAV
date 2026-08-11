/**
 * Appwrite Client Configuration
 * =============================
 * Централизованная конфигурация для Appwrite Cloud.
 * 
 * @see https://appwrite.io/docs
 */

import { Client, Account, TablesDB, Storage, Functions, type Models } from 'appwrite';

// ============================================================================
// Environment Configuration
// ============================================================================

const APPWRITE_ENDPOINT = import.meta.env.VITE_APPWRITE_ENDPOINT || 'https://fra.cloud.appwrite.io/v1';
const APPWRITE_PROJECT_ID = import.meta.env.VITE_APPWRITE_PROJECT_ID || '6a67d79d000fcca992f3';
const APPWRITE_DATABASE_ID = import.meta.env.VITE_APPWRITE_DATABASE_ID || 'yav';
const APPWRITE_USERS_TABLE_ID = import.meta.env.VITE_APPWRITE_USERS_TABLE_ID || 'users';
const APPWRITE_CHECKS_TABLE_ID = import.meta.env.VITE_APPWRITE_CHECKS_TABLE_ID || 'checks';
const APPWRITE_UPLOADS_BUCKET_ID =
  import.meta.env.VITE_APPWRITE_UPLOADS_BUCKET_ID || 'uploads';
const APPWRITE_ANALYZE_FUNCTION_ID =
  import.meta.env.VITE_APPWRITE_ANALYZE_FUNCTION_ID || 'analyze';

// ============================================================================
// Client Initialization
// ============================================================================

/**
 * Singleton Appwrite Client instance
 */
const client = new Client()
  .setEndpoint(APPWRITE_ENDPOINT)
  .setProject(APPWRITE_PROJECT_ID);

// ============================================================================
// Service Exports
// ============================================================================

/** Account service for auth operations */
export const account = new Account(client);

/** TablesDB service for server-backed profiles and check history */
export const tablesDB = new TablesDB(client);

/** Storage service for file operations */
export const storage = new Storage(client);

/** Functions service for serverless operations */
export const functions = new Functions(client);

// ============================================================================
// Database Configuration
// ============================================================================

/**
 * Appwrite resource identifiers
 */
export const APPWRITE_CONFIG = {
  /** Appwrite API endpoint and project */
  endpoint: APPWRITE_ENDPOINT,
  projectId: APPWRITE_PROJECT_ID,

  /** Main database ID */
  databaseId: APPWRITE_DATABASE_ID,
  
  /** Tables */
  tables: {
    checks: APPWRITE_CHECKS_TABLE_ID,
    users: APPWRITE_USERS_TABLE_ID,
  },
  
  /** Storage buckets */
  buckets: {
    uploads: APPWRITE_UPLOADS_BUCKET_ID,
  },
  
  /** Serverless functions */
  functions: {
    analyze: APPWRITE_ANALYZE_FUNCTION_ID,
  },
} as const;

// ============================================================================
// Type Exports
// ============================================================================

export { ID } from 'appwrite';
export type { Models };

// ============================================================================
// Client Export (for advanced usage)
// ============================================================================

export { client };
