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
}

export const api = new ApiClient();
