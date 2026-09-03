/**
 * web/js/api.js
 * Comprehensive REST API Client for Logovo.bet Prediction Platform (v2.0).
 */

import { tgBridge } from './tg.js';

class ApiClient {
  constructor() {
    this.baseUrl = window.location.origin;
    this.inFlight = new Map();
    this.cache = new Map();
  }

  async request(endpoint, options = {}) {
    const isGet = !options.method || options.method.toUpperCase() === 'GET';
    const cacheKey = endpoint;

    // Return cached response if valid within 5s
    if (isGet && this.cache.has(cacheKey)) {
      const entry = this.cache.get(cacheKey);
      if (Date.now() - entry.timestamp < 5000) {
        return entry.data;
      }
    }

    // Deduplicate in-flight concurrent requests
    if (isGet && this.inFlight.has(cacheKey)) {
      return this.inFlight.get(cacheKey);
    }

    const initData = tgBridge.getInitData();
    const headers = {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': initData,
      ...(options.headers || {})
    };

    const promise = (async () => {
      try {
        const res = await fetch(`${this.baseUrl}${endpoint}`, {
          ...options,
          headers
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.message || data.error || 'Ошибка запроса к серверу');
        }
        if (isGet) {
          this.cache.set(cacheKey, { data, timestamp: Date.now() });
        }
        return data;
      } catch (err) {
        console.error(`API Error [${endpoint}]:`, err);
        throw err;
      } finally {
        if (isGet) {
          this.inFlight.delete(cacheKey);
        }
      }
    })();

    if (isGet) {
      this.inFlight.set(cacheKey, promise);
    }

    return promise;
  }

  // 1. Bootstrap & Wallet
  getBootstrap() {
    return this.request('/api/bootstrap');
  }

  claimBonus() {
    return this.request('/api/bonus/claim', { method: 'POST' });
  }

  getWallet() {
    return this.request('/api/wallet');
  }

  getLeaderboard() {
    return this.request('/api/leaderboard');
  }

  // 2. Markets & Tours
  getTours(divisionId = null) {
    const q = divisionId ? `?division_id=${encodeURIComponent(divisionId)}` : '';
    return this.request(`/api/markets/tours${q}`);
  }

  getMatchMarkets(matchId) {
    return this.request(`/api/matches/${matchId}/markets`);
  }

  getOddsHistory(marketId, selectionKey) {
    const q = selectionKey ? `?selection_key=${encodeURIComponent(selectionKey)}` : '';
    return this.request(`/api/markets/${marketId}/odds-history${q}`);
  }

  // 3. Match Center 3.0
  getMatches(tour = null, status = null, divisionId = null, seasonId = null) {
    const params = new URLSearchParams();
    if (tour) params.append('tour', tour);
    if (status) params.append('status', status);
    if (divisionId) params.append('division_id', divisionId);
    if (seasonId) params.append('season_id', seasonId);
    const q = params.toString() ? `?${params.toString()}` : '';
    return this.request(`/api/matches${q}`);
  }

  getMatchDetail(matchId) {
    return this.request(`/api/matches/${matchId}`);
  }

  getMatchStats(matchId) {
    return this.request(`/api/matches/${matchId}/stats`);
  }

  getMatchH2H(matchId) {
    return this.request(`/api/matches/${matchId}/h2h`);
  }

  getMatchInsights(matchId) {
    return this.request(`/api/matches/${matchId}/insights`);
  }

  getMatchLive(matchId) {
    return this.request(`/api/matches/${matchId}/live`);
  }

  // 4. Predictions & Coupon Engine
  placePrediction(amount, selections, idempotencyKey = null) {
    return this.request('/api/predictions', {
      method: 'POST',
      body: JSON.stringify({ amount, selections, idempotency_key: idempotencyKey })
    });
  }

  getPredictions(status = null, limit = 30) {
    const params = new URLSearchParams();
    if (status && status !== 'all') params.append('status', status);
    if (limit) params.append('limit', limit);
    const q = params.toString() ? `?${params.toString()}` : '';
    return this.request(`/api/predictions${q}`);
  }

  getPredictionDetail(betId) {
    return this.request(`/api/predictions/${betId}`);
  }

  repeatPrediction(betId) {
    return this.request(`/api/predictions/${betId}/repeat`, { method: 'POST' });
  }

  // 5. Tournament Hub
  getTournaments() {
    return this.request('/api/tournaments');
  }

  getDivisions() {
    return this.request('/api/divisions');
  }

  getStandings(divisionId = null, seasonId = null) {
    const params = new URLSearchParams();
    if (divisionId) params.append('division_id', divisionId);
    if (seasonId) params.append('season_id', seasonId);
    const q = params.toString() ? `?${params.toString()}` : '';
    return this.request(`/api/standings${q}`);
  }

  getResults(divisionId = null, seasonId = null, limit = 30) {
    const params = new URLSearchParams();
    if (divisionId) params.append('division_id', divisionId);
    if (seasonId) params.append('season_id', seasonId);
    if (limit) params.append('limit', limit);
    const q = params.toString() ? `?${params.toString()}` : '';
    return this.request(`/api/results${q}`);
  }

  getTopScorers(tournamentId = 1) {
    return this.request(`/api/tournaments/${tournamentId}/top-scorers`);
  }

  // 6. User Stats, Saved Coupons, Favorites & Notifications
  getMyStats() {
    return this.request('/api/stats/me');
  }

  saveCoupon(name, selections, totalOdd) {
    return this.request('/api/saved-coupons', {
      method: 'POST',
      body: JSON.stringify({ name, selections, total_odd: totalOdd })
    });
  }

  getSavedCoupons() {
    return this.request('/api/saved-coupons');
  }

  deleteSavedCoupon(id) {
    return this.request(`/api/saved-coupons/${id}`, { method: 'DELETE' });
  }

  addFavorite(targetType, targetId) {
    return this.request('/api/favorites', {
      method: 'POST',
      body: JSON.stringify({ target_type: targetType, target_id: targetId })
    });
  }

  getFavorites() {
    return this.request('/api/favorites');
  }

  deleteFavorite(id) {
    return this.request(`/api/favorites/${id}`, { method: 'DELETE' });
  }

  getNotifications() {
    return this.request('/api/notifications');
  }

  markNotificationsRead() {
    return this.request('/api/notifications/read', { method: 'POST' });
  }

  // 7. Progression, Achievements & Profile
  getProgression() {
    return this.request('/api/progression');
  }

  getAchievements() {
    return this.request('/api/achievements');
  }

  claimAchievement(achId) {
    return this.request('/api/achievements/claim', {
      method: 'POST',
      body: JSON.stringify({ achievement_id: achId })
    });
  }

  getProfile(userId) {
    return this.request(`/api/profile/${userId}`);
  }
}

export const api = new ApiClient();
