/**
 * Centralized API client — eliminates duplicated fetch logic across 18+ pages.
 *
 * Features:
 * - Automatic JSON parsing with error propagation
 * - Configurable default fallbacks ([] for arrays, {} for objects, null)
 * - Batch fetch (Promise.all with per-request error isolation)
 * - Visible error states (no more silent .catch(() => {}))
 */

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Fetch JSON from API with proper error handling.
 * @param {string} url - API endpoint
 * @param {object} options - fetch options
 * @param {*} fallback - value to return on error (default: throw)
 * @returns {Promise<any>}
 */
export async function apiFetch(url, options = {}, fallback = undefined) {
  try {
    const res = await fetch(url, options);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new ApiError(body.detail || `HTTP ${res.status}`, res.status);
    }
    return await res.json();
  } catch (err) {
    if (fallback !== undefined) return fallback;
    throw err;
  }
}

/**
 * Batch fetch multiple endpoints in parallel with error isolation.
 * Each request that fails returns its fallback instead of failing the whole batch.
 *
 * @param {Array<{url: string, fallback?: any}>} requests
 * @returns {Promise<any[]>}
 */
export async function apiBatch(requests) {
  return Promise.all(
    requests.map(({ url, options, fallback }) =>
      apiFetch(url, options || {}, fallback !== undefined ? fallback : null)
    )
  );
}

/**
 * POST trigger (scan, audit, journal, learning).
 * Returns {ok: boolean, data?: any, error?: string}.
 */
export async function apiTrigger(url) {
  try {
    const res = await fetch(url, { method: "POST" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      return { ok: false, error: body.detail || `HTTP ${res.status}` };
    }
    const data = await res.json();
    return { ok: true, data };
  } catch (err) {
    return { ok: false, error: err.message || "Erreur réseau" };
  }
}

export { ApiError };
