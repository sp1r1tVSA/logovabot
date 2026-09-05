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
          const err = new Error(data.message || data.error || 'Ошибка запроса к серверу');
          err.status = res.status;
          err.data = data;
          err.code = data.error;
          if (data.error === 'LOGOVO_LOCKDOWN') {
            const lockScreen = document.getElementById('app-lockdown-screen');
            if (lockScreen) lockScreen.style.display = 'flex';
            const nav = document.querySelector('.bottom-nav');
            if (nav) nav.style.display = 'none';
            const drawer = document.getElementById('slip-drawer');
            if (drawer) drawer.style.display = 'none';
            const views = document.querySelector('.views-container');
            if (views) views.style.display = 'none';
            const header = document.querySelector('.app-header');
            if (header) header.style.display = 'none';
          }
          throw err;
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

  // 8. Phase 6: Live Center & Sports Intelligence
  getLiveMatches() {
    return this.request('/api/live');
  }

  getLiveMatch(id) {
    return this.request(`/api/live/${id}`);
  }

  getLiveEvents(id) {
    return this.request(`/api/live/${id}/events`);
  }

  getLiveStats(id) {
    return this.request(`/api/live/${id}/stats`);
  }

  getLiveMarkets(id) {
    return this.request(`/api/live/${id}/markets`);
  }

  getLiveIntelligence(id) {
    return this.request(`/api/live/${id}/intelligence`);
  }

  getOddsMovers() {
    return this.request('/api/odds/movers');
  }

  getHotMatches() {
    return this.request('/api/matches/hot');
  }

  getRecommendations() {
    return this.request('/api/recommendations');
  }

  getProfileAnalytics() {
    return this.request('/api/profile/analytics');
  }

  // Phase 7: AI & Sports Intelligence
  getIntelligenceMatches(divisionId = null, seasonId = null) {
    const p = new URLSearchParams();
    if (divisionId) p.append('division_id', divisionId);
    if (seasonId) p.append('season_id', seasonId);
    const q = p.toString() ? `?${p.toString()}` : '';
    return this.request(`/api/intelligence/matches${q}`);
  }

  getIntelligencePreview(matchId) {
    return this.request(`/api/intelligence/matches/${matchId}/preview`);
  }

  getIntelligencePrediction(matchId) {
    return this.request(`/api/intelligence/matches/${matchId}/prediction`);
  }

  getValueRadar(divisionId = null, seasonId = null, minEdge = 3.0) {
    const p = new URLSearchParams();
    if (divisionId) p.append('division_id', divisionId);
    if (seasonId) p.append('season_id', seasonId);
    if (minEdge) p.append('min_edge', minEdge);
    const q = p.toString() ? `?${p.toString()}` : '';
    return this.request(`/api/intelligence/value${q}`);
  }

  getIntelligencePerformance(divisionId = null, seasonId = null) {
    const p = new URLSearchParams();
    if (divisionId) p.append('division_id', divisionId);
    if (seasonId) p.append('season_id', seasonId);
    const q = p.toString() ? `?${p.toString()}` : '';
    return this.request(`/api/intelligence/performance${q}`);
  }

  // Phase 9: Cashout & Risk Management
  getCashoutQuote(betId) {
    return this.request(`/api/predictions/${betId}/cashout-quote`);
  }

  executeCashout(betId, idempotencyKey = null) {
    return this.request(`/api/predictions/${betId}/cashout`, {
      method: 'POST',
      body: JSON.stringify({ idempotency_key: idempotencyKey })
    });
  }

  getRiskExposure(params = {}) {
    const p = new URLSearchParams();
    if (params.division_id) p.append('division_id', params.division_id);
    if (params.market_id) p.append('market_id', params.market_id);
    if (params.season_id) p.append('season_id', params.season_id);
    const q = p.toString() ? `?${p.toString()}` : '';
    return this.request(`/api/admin/risk/exposure${q}`);
  }

  getRiskAlerts(params = {}) {
    const p = new URLSearchParams();
    if (params.division_id) p.append('division_id', params.division_id);
    if (params.status) p.append('status', params.status);
    if (params.severity) p.append('severity', params.severity);
    const q = p.toString() ? `?${p.toString()}` : '';
    return this.request(`/api/admin/risk/alerts${q}`);
  }

  getRiskLimits(divisionId = null) {
    const q = divisionId ? `?division_id=${divisionId}` : '';
    return this.request(`/api/admin/risk/limits${q}`);
  }

  // Phase 10: Profile 2.0, Fair Leaderboard & Seasonal Progression
  getPublicPlayerProfile(userId) {
    return this.request(`/api/player/${userId}/public`);
  }

  getProfileStats() {
    return this.request('/api/profile/stats');
  }

  getLeaderboard(params = {}) {
    const p = new URLSearchParams();
    if (params.page) p.append('page', params.page);
    if (params.limit) p.append('limit', params.limit);
    if (params.metric) p.append('metric', params.metric);
    if (params.period) p.append('period', params.period);
    if (params.season_id) p.append('season_id', params.season_id);
    const q = p.toString() ? `?${p.toString()}` : '';
    return this.request(`/api/leaderboard${q}`);
  }

  getDivisionLeaderboard(params = {}) {
    const p = new URLSearchParams();
    if (typeof params === 'number' || typeof params === 'string') {
      p.append('division_id', params);
    } else if (params && typeof params === 'object') {
      if (params.division_id) p.append('division_id', params.division_id);
      if (params.page) p.append('page', params.page);
      if (params.limit) p.append('limit', params.limit);
      if (params.metric) p.append('metric', params.metric);
    }
    const q = p.toString() ? `?${p.toString()}` : '';
    return this.request(`/api/leaderboard/division${q}`);
  }

  getSeasonLeaderboard(params = {}) {
    const p = new URLSearchParams();
    if (params.season_id) p.append('season_id', params.season_id);
    if (params.page) p.append('page', params.page);
    if (params.limit) p.append('limit', params.limit);
    if (params.metric) p.append('metric', params.metric);
    const q = p.toString() ? `?${p.toString()}` : '';
    return this.request(`/api/leaderboard/season${q}`);
  }

  getSeasonInfo() {
    return this.request('/api/season');
  }

  getSeasonRewards() {
    return this.request('/api/season/rewards');
  }

  getAdminSeason() {
    return this.request('/api/admin/season');
  }

  finalizeSeason(seasonId) {
    return this.request('/api/admin/season/finalize', {
      method: 'POST',
      body: JSON.stringify({ season_id: seasonId, confirm: true })
    });
  }
}

export const api = new ApiClient();
