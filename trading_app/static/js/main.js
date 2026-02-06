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
    initMarketBadges();

    // Refresh périodique
    setInterval(updateClock, 1000);
    setInterval(updateStatus, 60000);
    setInterval(updateMarketBadges, 60000);
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
            const statusDot = document.getElementById('status-dot');
            const statusText = document.getElementById('status-text');
            if (statusDot && statusText) {
                statusDot.classList.add('online');
                statusText.textContent = 'Online';
            }

            // Footer market status
            const marketStatusFooter = document.getElementById('market-status-footer');
            if (marketStatusFooter && data.marche_info) {
                marketStatusFooter.textContent = data.marche_info;
            }

            // Last update
            const lastUpdate = document.getElementById('last-update');
            if (lastUpdate) {
                lastUpdate.textContent = `MAJ: ${data.datetime}`;
            }
        }
    } catch (error) {
        console.error('Erreur status:', error);
        const statusDot = document.getElementById('status-dot');
        const statusText = document.getElementById('status-text');
        if (statusDot && statusText) {
            statusDot.classList.remove('online');
            statusText.textContent = 'Offline';
        }
    }
}

// ============================================================================
// MARKET BADGES
// ============================================================================

function initMarketBadges() {
    updateMarketBadges();
}

function updateMarketBadges() {
    const now = new Date();
    const parisTime = new Date(now.toLocaleString('en-US', { timeZone: 'Europe/Paris' }));
    const hour = parisTime.getHours();
    const minute = parisTime.getMinutes();
    const hourDecimal = hour + minute / 60;
    const dayOfWeek = parisTime.getDay();

    const badgeEU = document.getElementById('badge-eu');
    const badgeUS = document.getElementById('badge-us');

    if (!badgeEU || !badgeUS) return;

    // Weekend = fermé
    if (dayOfWeek === 0 || dayOfWeek === 6) {
        badgeEU.className = 'market-badge closed';
        badgeEU.textContent = 'EU Fermé';
        badgeUS.className = 'market-badge closed';
        badgeUS.textContent = 'US Fermé';
        return;
    }

    // Horaires EU: 9h-17h30
    if (hourDecimal >= 9 && hourDecimal < 17.5) {
        badgeEU.className = 'market-badge open';
        badgeEU.textContent = 'EU Ouvert';
    } else {
        badgeEU.className = 'market-badge closed';
        badgeEU.textContent = 'EU Fermé';
    }

    // Horaires US: 15h30-22h
    if (hourDecimal >= 15.5 && hourDecimal < 22) {
        badgeUS.className = 'market-badge open';
        badgeUS.textContent = 'US Ouvert';
    } else {
        badgeUS.className = 'market-badge closed';
        badgeUS.textContent = 'US Fermé';
    }
}

// ============================================================================
// UTILITAIRES
// ============================================================================

/**
 * Formate un nombre avec séparateur de milliers
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
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = message;

    const bgColor = type === 'success' ? 'var(--gradient-green)' :
                   type === 'error' ? 'var(--gradient-red)' :
                   'var(--gradient-blue)';

    toast.style.cssText = `
        position: fixed;
        bottom: 24px;
        right: 24px;
        padding: 16px 24px;
        background: ${bgColor};
        color: #0a0a0f;
        border-radius: 12px;
        font-weight: 600;
        z-index: 9999;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
        animation: slideInRight 0.3s ease;
    `;

    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'slideOutRight 0.3s ease';
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

const style = document.createElement('style');
style.textContent = `
    @keyframes slideInRight {
        from {
            transform: translateX(100%);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }

    @keyframes slideOutRight {
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

    @keyframes priceUp {
        0% { background-color: rgba(0, 255, 136, 0.3); }
        100% { background-color: transparent; }
    }

    @keyframes priceDown {
        0% { background-color: rgba(255, 71, 87, 0.3); }
        100% { background-color: transparent; }
    }

    .price-flash-up {
        animation: priceUp 0.5s ease;
    }

    .price-flash-down {
        animation: priceDown 0.5s ease;
    }

    /* Page header */
    .page-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 2rem;
    }

    .page-header h1 {
        font-size: 1.75rem;
        font-weight: 700;
        display: flex;
        align-items: center;
        gap: 0.75rem;
    }

    /* Periode selector */
    .periode-selector {
        display: flex;
        gap: 0.5rem;
        margin-bottom: 2rem;
        background: var(--glass-bg);
        padding: 0.35rem;
        border-radius: var(--radius-lg);
        border: 1px solid var(--border-color);
        width: fit-content;
    }

    .periode-btn {
        padding: 0.6rem 1.25rem;
        background: transparent;
        border: none;
        color: var(--text-secondary);
        font-size: 0.9rem;
        font-weight: 500;
        cursor: pointer;
        border-radius: var(--radius-md);
        transition: var(--transition-fast);
    }

    .periode-btn:hover {
        background: rgba(255, 255, 255, 0.05);
        color: var(--text-primary);
    }

    .periode-btn.active {
        background: var(--gradient-green);
        color: var(--bg-primary);
        font-weight: 600;
    }
`;
document.head.appendChild(style);

// ============================================================================
// EXPORTS
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
