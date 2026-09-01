/**
 * web/js/api.js
 * REST API Client for Logovo.bet Backend.
 */

import { tgBridge } from './tg.js';

class ApiClient {
  constructor() {
    this.baseUrl = window.location.origin;
  }

  async request(endpoint, options = {}) {
    const initData = tgBridge.getInitData();
    const headers = {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': initData,
      ...(options.headers || {})
    };

    try {
      const res = await fetch(`${this.baseUrl}${endpoint}`, {
        ...options,
        headers
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.message || data.error || 'Ошибка запроса к серверу');
      }
      return data;
    } catch (err) {
      console.error(`API Error [${endpoint}]:`, err);
      throw err;
    }
  }

  getBootstrap() {
    return this.request('/api/bootstrap');
  }

  getTours() {
    return this.request('/api/markets/tours');
  }

  claimBonus() {
    return this.request('/api/bonus/claim', { method: 'POST' });
  }

  placePrediction(amount, selections) {
    return this.request('/api/predictions', {
      method: 'POST',
      body: JSON.stringify({ amount, selections })
    });
  }

  getPredictions() {
    return this.request('/api/predictions');
  }

  getLeaderboard() {
    return this.request('/api/leaderboard');
  }

  // Gamification & Quests
  getProgression() {
    return this.request('/api/progression');
  }

  claimQuest(questId) {
    return this.request('/api/quests/claim', {
      method: 'POST',
      body: JSON.stringify({ quest_id: questId })
    });
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

  getDuels() {
    return this.request('/api/duels');
  }

  createDuel(payload) {
    return this.request('/api/duels/create', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  }

  acceptDuel(duelId, picks) {
    return this.request('/api/duels/accept', {
      method: 'POST',
      body: JSON.stringify({ duel_id: duelId, picks })
    });
  }

  getProfile(userId) {
    return this.request(`/api/profile/${userId}`);
  }
}

export const api = new ApiClient();
