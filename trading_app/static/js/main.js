/**
 * Agent Trading - JavaScript Principal
 * Fonctions communes pour toutes les pages
 */

// ============================================================================
// INITIALISATION
// ============================================================================

document.addEventListener('DOMContentLoaded', function() {
    initClock();
    initStatus();
    setInterval(updateClock, 1000);
    setInterval(updateStatus, 60000);
});

// ============================================================================
// HORLOGE
// ============================================================================

function initClock() {
    updateClock();
}

function updateClock() {
    const clockEl = document.getElementById('clock');
    if (clockEl) {
        const now = new Date();
        const options = {
            timeZone: 'Europe/Paris',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
        };
        clockEl.textContent = now.toLocaleTimeString('fr-FR', options);
    }
}

// ============================================================================
// STATUS
// ============================================================================

function initStatus() {
    updateStatus();
}

async function updateStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();

        if (data) {
            // Status dot
            const statusDot = document.querySelector('.status-dot');
            const statusText = document.getElementById('status-text');
            if (statusDot && statusText) {
                statusDot.classList.add('online');
                statusText.textContent = 'Online';
            }

            // Market status
            const marketStatus = document.getElementById('market-status');
            if (marketStatus && data.marche_info) {
                marketStatus.textContent = data.marche_info;
            }
        }
    } catch (error) {
        console.error('Erreur status:', error);
        const statusDot = document.querySelector('.status-dot');
        const statusText = document.getElementById('status-text');
        if (statusDot && statusText) {
            statusDot.classList.remove('online');
            statusText.textContent = 'Offline';
        }
    }
}

// ============================================================================
// UTILITAIRES
// ============================================================================

/**
 * Formate un nombre avec separateur de milliers
 */
function formatNumber(num, decimals = 2) {
    if (num === null || num === undefined) return '-';
    return num.toLocaleString('fr-FR', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
    });
}

/**
 * Formate une variation avec signe et couleur
 */
function formatVariation(variation) {
    if (variation === null || variation === undefined) return '-';
    const sign = variation >= 0 ? '+' : '';
    return `${sign}${variation.toFixed(2)}%`;
}

/**
 * Retourne la classe CSS pour une variation
 */
function getVariationClass(variation) {
    if (variation > 0) return 'positive';
    if (variation < 0) return 'negative';
    return '';
}

/**
 * Formate une date
 */
function formatDate(dateStr) {
    const date = new Date(dateStr);
    return date.toLocaleDateString('fr-FR', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric'
    });
}

/**
 * Formate une heure
 */
function formatTime(dateStr) {
    const date = new Date(dateStr);
    return date.toLocaleTimeString('fr-FR', {
        hour: '2-digit',
        minute: '2-digit'
    });
}

/**
 * Debounce function
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Affiche une notification toast
 */
function showToast(message, type = 'info') {
    // Creer element toast
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;

    // Style
    toast.style.cssText = `
        position: fixed;
        bottom: 20px;
        right: 20px;
        padding: 12px 24px;
        background: ${type === 'success' ? '#00ff88' : type === 'error' ? '#ff4757' : '#58a6ff'};
        color: ${type === 'success' || type === 'error' ? '#0d1117' : '#0d1117'};
        border-radius: 8px;
        font-weight: 500;
        z-index: 9999;
        animation: slideIn 0.3s ease;
    `;

    document.body.appendChild(toast);

    // Auto remove
    setTimeout(() => {
        toast.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ============================================================================
// API HELPERS
// ============================================================================

/**
 * Fetch avec gestion d'erreur
 */
async function apiFetch(url, options = {}) {
    try {
        const response = await fetch(url, {
            ...options,
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            }
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        return await response.json();
    } catch (error) {
        console.error('API Error:', error);
        throw error;
    }
}

/**
 * GET request
 */
async function apiGet(url) {
    return apiFetch(url);
}

/**
 * POST request
 */
async function apiPost(url, data) {
    return apiFetch(url, {
        method: 'POST',
        body: JSON.stringify(data)
    });
}

// ============================================================================
// ANIMATIONS CSS
// ============================================================================

// Ajouter les keyframes pour les animations
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from {
            transform: translateX(100%);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }

    @keyframes slideOut {
        from {
            transform: translateX(0);
            opacity: 1;
        }
        to {
            transform: translateX(100%);
            opacity: 0;
        }
    }

    @keyframes fadeIn {
        from { opacity: 0; }
        to { opacity: 1; }
    }

    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.5; }
    }
`;
document.head.appendChild(style);

// ============================================================================
// EXPORTS (pour utilisation dans d'autres scripts)
// ============================================================================

window.TradingApp = {
    formatNumber,
    formatVariation,
    getVariationClass,
    formatDate,
    formatTime,
    debounce,
    showToast,
    apiGet,
    apiPost
};
