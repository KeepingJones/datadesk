// utils.js - shared utility functions for DataDesk dashboard
// Simple in-memory cache for API responses
const cache = {};

/**
 * Fetch JSON with in-memory caching.
 * @param {string} url - API endpoint URL.
 * @param {number} ttl - Time to live in milliseconds (default 5 minutes).
 */
function fetchWithCache(url, ttl = 5 * 60 * 1000) {
    const now = Date.now();
    const entry = cache[url];
    if (entry && (now - entry.timestamp) < ttl) {
        return Promise.resolve(entry.data);
    }
    return fetch(url)
        .then(r => r.json())
        .then(data => {
            cache[url] = { timestamp: now, data };
            return data;
        });
}

/**
 * Update UI only when the data has changed.
 * @param {string} key - Cache key
 * @param {any} newData - New data to compare
 * @param {Function} render - Callback to render UI when data changes
 */
function updateIfChanged(key, newData, render) {
    const oldData = cache[key];
    if (JSON.stringify(oldData) !== JSON.stringify(newData)) {
        cache[key] = newData;
        render();
    }
}
