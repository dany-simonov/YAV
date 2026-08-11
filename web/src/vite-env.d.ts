/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APPWRITE_ENDPOINT: string;
  readonly VITE_APPWRITE_PROJECT_ID: string;
  readonly VITE_APPWRITE_DATABASE_ID: string;
  readonly VITE_APPWRITE_USERS_TABLE_ID: string;
  readonly VITE_APPWRITE_CHECKS_TABLE_ID: string;
  readonly VITE_APPWRITE_UPLOADS_BUCKET_ID: string;
  readonly VITE_APPWRITE_ANALYZE_FUNCTION_ID: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
