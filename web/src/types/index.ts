/**
 * Type Definitions
 * ================
 * Централизованные типы для всего приложения.
 */

// ============================================================================
// Enums
// ============================================================================

export type Verdict = 'REAL' | 'FAKE' | 'UNCERTAIN';
export type MediaType = 'image' | 'audio' | 'video' | 'text';

// ============================================================================
// Analysis Types
// ============================================================================

export interface Check {
  id: string;
  media_type: MediaType;
  verdict: Verdict;
  confidence: number;
  model_used: string;
  explanation: string;
  processing_ms: number;
  created_at: string;
}

export interface CheckResult {
  verdict: Verdict;
  confidence: number;
  model_used: string;
  explanation: string;
  processing_ms: number;
  media_type: MediaType;
  short_report?: string | null;
}

// Backward-compatible type for older components/hooks.
export interface AnalyzeResult {
  verdict: Verdict;
  confidence: number;
  model_used: string;
  explanation: string;
  processing_ms: number;
}

// Hybrid text analysis
export type HybridTokenType = 'normal' | 'manipulation' | 'fake' | 'plagiarism';

export interface FactCheckItem {
  exact_quote: string;
  status: string;
  truth: string;
  source_url: string;
}

export interface HybridToken {
  text: string;
  type: HybridTokenType;
  details?: {
    truth?: string;
    source_url?: string;
  };
}

export interface HybridTextResult {
  verdict: string;
  ai_verdict: string;
  ai_confidence: number;
  model_used: string;
  processing_ms: number;
  fact_checks: FactCheckItem[];
  tokens: HybridToken[];
  truncated?: boolean;
}

// ============================================================================
// User Types
// ============================================================================

export interface User {
  $id: string;
  email: string;
  name: string;
  emailVerification?: boolean;
  phone?: string;
  phoneVerification?: boolean;
  createdAt?: string;
}

export interface UserStats {
  user_id: string;
  is_premium: boolean;
  checks_today: number;
  daily_limit: number;
  total_checks: number;
  created_at: string;
}

// ============================================================================
// Form Types
// ============================================================================

export interface LoginFormData {
  email: string;
  password: string;
}

export interface RegisterFormData {
  name: string;
  email: string;
  password: string;
  confirmPassword: string;
}

// ============================================================================
// Validation Types
// ============================================================================

export interface ValidationError {
  field: string;
  message: string;
}

export interface FormState<T> {
  data: T;
  errors: ValidationError[];
  isSubmitting: boolean;
}

// ============================================================================
// Upload Types
// ============================================================================

export interface UploadFile {
  id: string;
  file: File;
  preview?: string;
  progress: number;
  status: 'pending' | 'uploading' | 'analyzing' | 'complete' | 'error';
  result?: CheckResult;
  error?: string;
}

// ============================================================================
// UI Types
// ============================================================================

export type TabType = 'media' | 'text';

export interface Tab {
  id: TabType;
  label: string;
  icon: React.ReactNode;
}

// ============================================================================
// API Response Types
// ============================================================================

export interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  limit: number;
  hasMore: boolean;
}
