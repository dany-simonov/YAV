import { describe, expect, it } from 'vitest';

import type { User } from '../../store';
import {
  getProtectedRouteRedirect,
  getPublicOnlyRedirect,
  getVerificationRouteRedirect,
} from './ProtectedRoute';

const user = (verified: boolean): User => ({
  $id: 'user-1',
  email: 'user@example.test',
  name: 'User',
  emailVerification: verified,
  phone: '',
  phoneVerification: false,
  createdAt: '2026-08-08T12:00:00.000Z',
});

describe('auth route guards', () => {
  it('routes anonymous users to login', () => {
    expect(getProtectedRouteRedirect(null)).toBe('/login');
    expect(getPublicOnlyRedirect(null)).toBeNull();
    expect(getVerificationRouteRedirect(null)).toBe('/login');
  });

  it('routes unverified users to verification and allows its page', () => {
    expect(getProtectedRouteRedirect(user(false))).toBe('/verify-email');
    expect(getPublicOnlyRedirect(user(false))).toBe('/verify-email');
    expect(getVerificationRouteRedirect(user(false))).toBeNull();
  });

  it('allows verified dashboard access and leaves verification page', () => {
    expect(getProtectedRouteRedirect(user(true))).toBeNull();
    expect(getPublicOnlyRedirect(user(true))).toBe('/dashboard');
    expect(getVerificationRouteRedirect(user(true))).toBe('/dashboard');
  });
});
