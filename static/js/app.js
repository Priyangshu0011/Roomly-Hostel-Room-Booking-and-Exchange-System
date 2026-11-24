document.addEventListener('DOMContentLoaded', () => {
    const navToggle = document.querySelector('.nav-toggle');
    const nav = document.getElementById('primary-nav');

    if (navToggle && nav) {
        navToggle.addEventListener('click', () => {
            const expanded = navToggle.getAttribute('aria-expanded') === 'true';
            navToggle.setAttribute('aria-expanded', String(!expanded));
            nav.classList.toggle('is-open', !expanded);
        });
    }

    document.querySelectorAll('.alert-close').forEach((button) => {
        button.addEventListener('click', () => button.closest('.alert').remove());
    });

    document.querySelectorAll('[data-password-toggle]').forEach((button) => {
        button.addEventListener('click', () => {
            const input = document.getElementById(button.getAttribute('aria-controls'));
            if (!input) return;
            const showing = input.type === 'text';
            input.type = showing ? 'password' : 'text';
            button.textContent = showing ? 'Show' : 'Hide';
            button.setAttribute('aria-pressed', String(!showing));
        });
    });

    const countdowns = document.querySelectorAll('[data-countdown]');
    const updateCountdowns = () => {
        countdowns.forEach((element) => {
            const remainingSeconds = Math.max(
                0,
                Math.floor((Date.parse(element.dataset.countdown) - Date.now()) / 1000),
            );
            if (remainingSeconds === 0) {
                element.textContent = 'Expired';
                return;
            }
            const days = Math.floor(remainingSeconds / 86400);
            const hours = Math.floor((remainingSeconds % 86400) / 3600);
            const minutes = Math.floor((remainingSeconds % 3600) / 60);
            const duration = days ? `${days}d ${hours}h` : `${hours}h ${minutes}m`;
            element.textContent = `Expires in ${duration}`;
        });
    };
    if (countdowns.length) {
        updateCountdowns();
        window.setInterval(updateCountdowns, 30000);
    }
});
