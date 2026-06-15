// Smooth scrolling for in-page navigation (header links and CTA buttons)
document.querySelectorAll('.doc-nav a[href^="#"], [data-scroll-to]').forEach((trigger) => {
    trigger.addEventListener('click', (event) => {
        const targetId = trigger.getAttribute('href') || trigger.getAttribute('data-scroll-to');
        if (!targetId || targetId === '#') return;
        const targetEl = document.querySelector(targetId);
        if (!targetEl) return;
        event.preventDefault();
        targetEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
});

// Collapsible feature cards
document.querySelectorAll('[data-collapsible]').forEach((card, index) => {
    const body = card.querySelector('.feature-card-body');
    const toggle = card.querySelector('.feature-toggle');
    if (!body || !toggle) return;

    // Start with some cards collapsed on load
    if (index > 1) {
        card.classList.add('collapsed');
    }

    toggle.addEventListener('click', () => {
        card.classList.toggle('collapsed');
    });
});

// Reveal on scroll
const revealTargets = document.querySelectorAll(
    '.section, .feature-card, .workflow-card, .timeline-step, .technical-card, .metric-card, .chart-card, .process-card'
);

revealTargets.forEach((el) => el.classList.add('reveal'));

const observer = new IntersectionObserver(
    (entries) => {
        entries.forEach((entry) => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                observer.unobserve(entry.target);
            }
        });
    },
    {
        threshold: 0.18,
    }
);

revealTargets.forEach((el) => observer.observe(el));

// Simple interactive diagram highlight (hover to emphasize nodes)
document.querySelectorAll('.sync-node').forEach((node) => {
    node.addEventListener('mouseenter', () => {
        node.style.transform = 'translateY(-2px) scale(1.02)';
        node.style.boxShadow = '0 16px 40px rgba(15, 23, 42, 1)';
    });
    node.addEventListener('mouseleave', () => {
        node.style.transform = '';
        node.style.boxShadow = '';
    });
});

// Active nav item based on scroll position
const sections = Array.from(document.querySelectorAll('main > .section'));
const navLinks = Array.from(document.querySelectorAll('.doc-nav a[href^="#"]'));

function updateActiveNav() {
    let currentId = null;
    const scrollPos = window.scrollY || window.pageYOffset;
    sections.forEach((section) => {
        const rect = section.getBoundingClientRect();
        const top = rect.top + scrollPos - 120; // offset for sticky header
        if (scrollPos >= top) {
            currentId = section.id;
        }
    });
    if (!currentId) return;
    navLinks.forEach((link) => {
        const href = link.getAttribute('href');
        if (href === `#${currentId}`) {
            link.classList.add('active');
        } else {
            link.classList.remove('active');
        }
    });
}

window.addEventListener('scroll', updateActiveNav, { passive: true });
window.addEventListener('load', updateActiveNav);

// Animated counters for dashboard metrics
const counterElements = document.querySelectorAll('[data-counter]');

const animateCounter = (el) => {
    const target = Number(el.getAttribute('data-target') || '0');
    if (!target) return;
    const duration = 1400;
    const start = performance.now();

    const step = (now) => {
        const progress = Math.min((now - start) / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3); // easeOutCubic
        const value = Math.floor(target * eased);
        el.textContent = value.toLocaleString();
        if (progress < 1) {
            requestAnimationFrame(step);
        }
    };

    requestAnimationFrame(step);
};

if (counterElements.length) {
    const counterObserver = new IntersectionObserver(
        (entries) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    animateCounter(entry.target);
                    counterObserver.unobserve(entry.target);
                }
            });
        },
        { threshold: 0.4 }
    );

    counterElements.forEach((el) => counterObserver.observe(el));
}

// Simple canvas charts (lightweight replacement instead of full Chart.js)
function drawLineChart(canvasId, color, points) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !canvas.getContext) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;

    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = 'rgba(15,23,42,0.95)';
    ctx.fillRect(0, 0, w, h);

    ctx.strokeStyle = 'rgba(55,65,81,0.7)';
    ctx.lineWidth = 1;
    const gridLines = 4;
    for (let i = 1; i < gridLines; i++) {
        const y = (h / gridLines) * i;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
    }

    const maxVal = Math.max(...points);
    const minVal = 0;
    const stepX = w / (points.length - 1 || 1);

    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    points.forEach((val, idx) => {
        const x = stepX * idx;
        const norm = (val - minVal) / (maxVal || 1);
        const y = h - norm * (h * 0.7) - h * 0.15;
        if (idx === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // gradient under curve
    const gradient = ctx.createLinearGradient(0, 0, 0, h);
    gradient.addColorStop(0, color.replace('1)', '0.35)'));
    gradient.addColorStop(1, 'rgba(15,23,42,0)');
    ctx.lineTo(w, h);
    ctx.lineTo(0, h);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();
}

function drawBarChart(canvasId, color, points) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !canvas.getContext) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;

    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = 'rgba(15,23,42,0.95)';
    ctx.fillRect(0, 0, w, h);

    const maxVal = Math.max(...points) || 1;
    const barWidth = w / (points.length * 1.6);
    const gap = barWidth * 0.6;
    let x = gap;

    points.forEach((val) => {
        const barHeight = (val / maxVal) * (h * 0.7);
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.roundRect(x, h - barHeight - 10, barWidth, barHeight, 4);
        ctx.fill();
        x += barWidth + gap;
    });
}

// Initialize charts when section enters view
const chartSection = document.getElementById('charts');
if (chartSection) {
    const chartObserver = new IntersectionObserver(
        (entries) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    drawLineChart('queueChart', 'rgba(129,140,248,1)', [5, 12, 9, 15, 22, 18, 24]);
                    drawBarChart('webhookChart', 'rgba(45,212,191,1)', [120, 140, 110, 160, 150, 170, 155]);
                    drawLineChart('latencyChart', 'rgba(248,250,252,1)', [900, 750, 680, 720, 640, 610, 580]);
                    chartObserver.unobserve(chartSection);
                }
            });
        },
        { threshold: 0.25 }
    );

    chartObserver.observe(chartSection);
}

// Animated process steps: highlight steps sequentially when visible
document.querySelectorAll('.process-steps').forEach((list) => {
    const steps = Array.from(list.querySelectorAll('li'));
    if (!steps.length) return;

    const activateSteps = () => {
        steps.forEach((step, idx) => {
            setTimeout(() => {
                step.classList.add('active');
            }, idx * 230);
        });
    };

    const processObserver = new IntersectionObserver(
        (entries) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    activateSteps();
                    processObserver.unobserve(list);
                }
            });
        },
        { threshold: 0.4 }
    );

    processObserver.observe(list);
});

